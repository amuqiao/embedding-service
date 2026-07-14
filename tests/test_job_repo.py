import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.dialects import postgresql

from app.core.error_registry import get_error_spec
from app.models.job import CallbackOutbox, DispatchOutbox, Job, JobAttempt, JobEvent, JobSubmissionKey
from app.repositories.job_repo import JobRepo


class _CleanupResult:
    rowcount = 3


class _RowCountResult:
    def __init__(self, rowcount):
        self.rowcount = rowcount


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


class _NestedTransaction:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        self.db.nested_begins += 1
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.db.nested_commits += 1
        else:
            self.db.nested_rollbacks += 1
        return False


class _FakeDB:
    def __init__(self):
        self.statements = []
        self.parameters = []
        self.results = []
        self.flushed = False
        self.added = []
        self.nested_begins = 0
        self.nested_commits = 0
        self.nested_rollbacks = 0

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

    def begin_nested(self):
        return _NestedTransaction(self)


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


def _job_params_ref(payload: dict | None = None) -> dict:
    return {
        "storage": "db_inline",
        "type": "json",
        "name": "job_params",
        "payload": payload or {},
    }


def _job_params_hash() -> str:
    return "sha256:test-job-params"


def _create_kwargs(**overrides):
    values = {
        "job_params_ref": _job_params_ref(),
        "job_params_hash": _job_params_hash(),
    }
    values.update(overrides)
    return values


def _attempt(
    *,
    id: uuid.UUID | None = None,
    job_id: uuid.UUID | None = None,
    purpose: str = "business_execution",
    purpose_attempt_no: int = 1,
    status: str = "pending",
    lease_token: uuid.UUID | None = None,
    timeout_seconds: int = 60,
    policy_max_attempts: int = 1,
    policy_retry_delay_seconds: int | None = None,
    policy_backoff_kind: str | None = None,
) -> JobAttempt:
    attempt_id = id or uuid.uuid4()
    retry_delay_seconds = policy_retry_delay_seconds
    if retry_delay_seconds is None and policy_max_attempts > 1:
        retry_delay_seconds = 5
    backoff_kind = policy_backoff_kind or ("fixed" if policy_max_attempts > 1 else "none")
    retryable_error_codes = ["JOB_TIMEOUT"] if policy_max_attempts > 1 else []
    return JobAttempt(
        id=attempt_id,
        job_id=job_id or uuid.uuid4(),
        purpose=purpose,
        purpose_attempt_no=purpose_attempt_no,
        retry_chain_id=attempt_id,
        created_reason="initial",
        status=status,
        lease_token=lease_token,
        timeout_seconds=timeout_seconds,
        policy_max_attempts=policy_max_attempts,
        policy_retry_delay_seconds=retry_delay_seconds,
        policy_backoff_kind=backoff_kind,
        policy_retryable_error_codes=retryable_error_codes,
        retry_policy_snapshot={
            "max_attempts": policy_max_attempts,
            "retry_delay_seconds": retry_delay_seconds,
            "backoff_kind": backoff_kind,
            "retryable_error_codes": retryable_error_codes,
        },
    )


@pytest.mark.asyncio
async def test_cleanup_expired_jobs_soft_deletes_only_settled_terminal_jobs():
    db = _FakeDB()
    root_id = uuid.uuid4()
    db.results.extend([_ScalarListResult([root_id]), _RowCountResult(1), _CleanupResult()])

    rowcount = await JobRepo.cleanup_expired_jobs(db)

    assert rowcount == 3
    assert db.nested_begins == 1
    assert db.nested_commits == 1
    assert db.nested_rollbacks == 0
    assert len(db.statements) == 3
    root_select_sql = _compile(db.statements[0])
    assert "SELECT job_aggregates.id" in root_select_sql
    assert "FOR UPDATE SKIP LOCKED" in root_select_sql
    assert "job_submission_keys.deleted_at IS NULL" in root_select_sql
    assert "callback_outbox.event_type =" in root_select_sql
    submission_key_update = db.statements[1]
    assert submission_key_update.__visit_name__ == "update"
    submission_key_sql = _compile(submission_key_update)
    assert "UPDATE job_submission_keys SET" in submission_key_sql
    assert "deleted_at=now()" in submission_key_sql
    assert "deleted_reason=" in submission_key_sql
    assert "job_submission_keys.deleted_at IS NULL" in submission_key_sql

    job_update = db.statements[2]
    assert job_update.__visit_name__ == "update"

    job_sql = _compile(job_update)
    assert "UPDATE job_aggregates SET" in job_sql
    assert "deleted_at" in job_sql
    assert "job_aggregates.id IN" in job_sql
    assert "job_aggregates.root_job_id IN" in job_sql
    assert "job_submission_keys" not in job_sql
    assert "job_aggregates.deleted_at IS NULL" in job_sql


@pytest.mark.asyncio
async def test_cleanup_expired_jobs_returns_zero_without_candidates():
    db = _FakeDB()
    db.results.append(_ScalarListResult([]))

    rowcount = await JobRepo.cleanup_expired_jobs(db)

    assert rowcount == 0
    assert len(db.statements) == 1
    assert db.flushed is False
    assert db.nested_begins == 1
    assert db.nested_commits == 1
    assert db.nested_rollbacks == 0


@pytest.mark.asyncio
async def test_cleanup_expired_jobs_rolls_back_when_submission_key_count_mismatches():
    db = _FakeDB()
    db.results.extend([_ScalarListResult([uuid.uuid4(), uuid.uuid4()]), _RowCountResult(1)])

    with pytest.raises(ValueError, match="active submission key count mismatch"):
        await JobRepo.cleanup_expired_jobs(db)

    assert len(db.statements) == 2
    assert db.flushed is False
    assert db.nested_begins == 1
    assert db.nested_commits == 0
    assert db.nested_rollbacks == 1


@pytest.mark.asyncio
async def test_cleanup_expired_jobs_rolls_back_when_family_update_affects_no_jobs():
    db = _FakeDB()
    db.results.extend([_ScalarListResult([uuid.uuid4()]), _RowCountResult(1), _RowCountResult(0)])

    with pytest.raises(ValueError, match="family update affected fewer rows than roots"):
        await JobRepo.cleanup_expired_jobs(db)

    assert len(db.statements) == 3
    assert db.flushed is False
    assert db.nested_begins == 1
    assert db.nested_commits == 0
    assert db.nested_rollbacks == 1


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
    assert "job_submission_keys.deleted_at IS NULL" in sql
    assert "job_aggregates.deleted_at IS NULL" in sql
    assert "job_aggregates.root_job_id IS NULL" in sql
    assert "job_aggregates.workflow_node_key IS NULL" in sql


@pytest.mark.asyncio
async def test_soft_delete_root_family_updates_family_and_submission_key():
    root_id = uuid.uuid4()
    now = datetime.now(UTC)
    root = Job(
        id=root_id,
        caller_id="caller-1",
        client_request_id="request-1",
        job_type="test.echo",
        status="failed",
        progress_percent=0,
        metadata_={},
        job_params_ref=_job_params_ref(),
        job_params_hash=_job_params_hash(),
    )
    db = _FakeDB()
    db.results.extend([_ScalarResult(root), _RowCountResult(1), _CleanupResult()])

    rowcount = await JobRepo.soft_delete_root_family(db, root_id, reason="manual", now=now)

    assert rowcount == 3
    assert db.nested_begins == 1
    assert db.nested_commits == 1
    assert db.nested_rollbacks == 0
    assert len(db.statements) == 3
    root_select_sql = _compile(db.statements[0])
    key_update_sql = _compile(db.statements[1])
    family_update_sql = _compile(db.statements[2])
    assert "job_aggregates.root_job_id IS NULL" in root_select_sql
    assert "job_aggregates.workflow_node_key IS NULL" in root_select_sql
    assert "job_aggregates.status IN" in root_select_sql
    assert "job_aggregates.active_attempt_id IS NULL" in root_select_sql
    assert "job_aggregates.status =" in root_select_sql
    assert "callback_outbox.event_type =" in root_select_sql
    assert "job_submission_keys.deleted_at IS NULL" in root_select_sql
    assert "UPDATE job_aggregates SET" in family_update_sql
    assert "job_aggregates.id =" in family_update_sql
    assert "job_aggregates.root_job_id =" in family_update_sql
    assert "job_aggregates.deleted_at IS NULL" in family_update_sql
    assert "UPDATE job_submission_keys SET" in key_update_sql
    assert "job_submission_keys.job_id =" in key_update_sql
    assert "job_submission_keys.deleted_at IS NULL" in key_update_sql


@pytest.mark.asyncio
async def test_soft_delete_root_family_returns_zero_when_root_not_eligible():
    db = _FakeDB()
    db.results.append(_ScalarResult(None))

    rowcount = await JobRepo.soft_delete_root_family(db, uuid.uuid4(), reason="manual")

    assert rowcount == 0
    assert len(db.statements) == 1


@pytest.mark.asyncio
async def test_soft_delete_root_family_requires_active_submission_key_update():
    root_id = uuid.uuid4()
    root = Job(
        id=root_id,
        caller_id="caller-1",
        client_request_id="request-1",
        job_type="test.echo",
        status="failed",
        progress_percent=0,
        metadata_={},
        job_params_ref=_job_params_ref(),
        job_params_hash=_job_params_hash(),
    )
    db = _FakeDB()
    db.results.extend([_ScalarResult(root), _RowCountResult(0)])

    with pytest.raises(ValueError, match="active submission key is missing"):
        await JobRepo.soft_delete_root_family(db, root_id, reason="manual")

    assert db.nested_begins == 1
    assert db.nested_commits == 0
    assert db.nested_rollbacks == 1


@pytest.mark.asyncio
async def test_soft_delete_root_family_rolls_back_when_family_update_affects_no_jobs():
    root_id = uuid.uuid4()
    root = Job(
        id=root_id,
        caller_id="caller-1",
        client_request_id="request-1",
        job_type="test.echo",
        status="failed",
        progress_percent=0,
        metadata_={},
        job_params_ref=_job_params_ref(),
        job_params_hash=_job_params_hash(),
    )
    db = _FakeDB()
    db.results.extend([_ScalarResult(root), _RowCountResult(1), _RowCountResult(0)])

    with pytest.raises(ValueError, match="family update affected no jobs"):
        await JobRepo.soft_delete_root_family(db, root_id, reason="manual")

    assert db.flushed is False
    assert db.nested_begins == 1
    assert db.nested_commits == 0
    assert db.nested_rollbacks == 1


@pytest.mark.asyncio
async def test_restore_root_family_requires_deleted_submission_key():
    root_id = uuid.uuid4()
    root = Job(
        id=root_id,
        caller_id="caller-1",
        client_request_id="request-1",
        job_type="test.echo",
        status="failed",
        progress_percent=0,
        metadata_={},
        job_params_ref=_job_params_ref(),
        job_params_hash=_job_params_hash(),
        deleted_at=datetime.now(UTC),
    )
    db = _FakeDB()
    db.results.extend(
        [
            _ScalarResult(root),
            _ScalarResult(None),
            _ScalarListResult([]),
        ]
    )

    with pytest.raises(ValueError, match="deleted submission key is missing"):
        await JobRepo.restore_root_family(db, root_id)


@pytest.mark.asyncio
async def test_restore_root_family_rejects_partially_deleted_family():
    root_id = uuid.uuid4()
    root = Job(
        id=root_id,
        caller_id="caller-1",
        client_request_id="request-1",
        job_type="test.echo",
        status="failed",
        progress_percent=0,
        metadata_={},
        job_params_ref=_job_params_ref(),
        job_params_hash=_job_params_hash(),
        deleted_at=datetime.now(UTC),
    )
    db = _FakeDB()
    db.results.extend(
        [
            _ScalarResult(root),
            _ScalarResult(uuid.uuid4()),
        ]
    )

    with pytest.raises(ValueError, match="family is only partially soft-deleted"):
        await JobRepo.restore_root_family(db, root_id)


@pytest.mark.asyncio
async def test_restore_root_family_rejects_active_submission_key_conflict():
    root_id = uuid.uuid4()
    root = Job(
        id=root_id,
        caller_id="caller-1",
        client_request_id="request-1",
        job_type="test.echo",
        status="failed",
        progress_percent=0,
        metadata_={},
        job_params_ref=_job_params_ref(),
        job_params_hash=_job_params_hash(),
        deleted_at=datetime.now(UTC),
    )
    key = JobSubmissionKey(
        id=uuid.uuid4(),
        caller_id="caller-1",
        key_kind="client_request_id",
        key_value="request-1",
        request_fingerprint="sha256:" + "a" * 64,
        job_id=root_id,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        deleted_at=datetime.now(UTC),
    )
    db = _FakeDB()
    db.results.extend(
        [
            _ScalarResult(root),
            _ScalarResult(None),
            _ScalarListResult([key]),
            _CleanupResult(),
            _ScalarResult(uuid.uuid4()),
        ]
    )

    with pytest.raises(ValueError, match="submission key is already used"):
        await JobRepo.restore_root_family(db, root_id)


@pytest.mark.asyncio
async def test_restore_root_family_locks_submission_key_and_restores_family():
    root_id = uuid.uuid4()
    root = Job(
        id=root_id,
        caller_id="caller-1",
        client_request_id="request-1",
        job_type="test.echo",
        status="failed",
        progress_percent=0,
        metadata_={},
        job_params_ref=_job_params_ref(),
        job_params_hash=_job_params_hash(),
        deleted_at=datetime.now(UTC),
    )
    key = JobSubmissionKey(
        id=uuid.uuid4(),
        caller_id="caller-1",
        key_kind="client_request_id",
        key_value="request-1",
        request_fingerprint="sha256:" + "a" * 64,
        job_id=root_id,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        deleted_at=datetime.now(UTC),
    )
    db = _FakeDB()
    db.results.extend(
        [
            _ScalarResult(root),
            _ScalarResult(None),
            _ScalarListResult([key]),
            _CleanupResult(),
            _ScalarResult(None),
            _RowCountResult(3),
            _RowCountResult(1),
        ]
    )

    rowcount = await JobRepo.restore_root_family(db, root_id)

    assert rowcount == 3
    assert db.nested_begins == 1
    assert db.nested_commits == 1
    assert db.nested_rollbacks == 0
    lock_sql = _compile(db.statements[3])
    assert "pg_advisory_xact_lock" in lock_sql
    family_update_sql = _compile(db.statements[5])
    key_update_sql = _compile(db.statements[6])
    assert "UPDATE job_aggregates SET" in family_update_sql
    assert "deleted_at=%(deleted_at)s" in family_update_sql
    assert "UPDATE job_submission_keys SET" in key_update_sql
    assert "deleted_at=%(deleted_at)s" in key_update_sql


@pytest.mark.asyncio
async def test_restore_root_family_rolls_back_when_submission_key_restore_count_mismatches():
    root_id = uuid.uuid4()
    root = Job(
        id=root_id,
        caller_id="caller-1",
        client_request_id="request-1",
        job_type="test.echo",
        status="failed",
        progress_percent=0,
        metadata_={},
        job_params_ref=_job_params_ref(),
        job_params_hash=_job_params_hash(),
        deleted_at=datetime.now(UTC),
    )
    key = JobSubmissionKey(
        id=uuid.uuid4(),
        caller_id="caller-1",
        key_kind="client_request_id",
        key_value="request-1",
        request_fingerprint="sha256:" + "a" * 64,
        job_id=root_id,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        deleted_at=datetime.now(UTC),
    )
    db = _FakeDB()
    db.results.extend(
        [
            _ScalarResult(root),
            _ScalarResult(None),
            _ScalarListResult([key]),
            _CleanupResult(),
            _ScalarResult(None),
            _RowCountResult(3),
            _RowCountResult(0),
        ]
    )

    with pytest.raises(ValueError, match="deleted submission key restore count mismatch"):
        await JobRepo.restore_root_family(db, root_id)

    assert db.flushed is False
    assert db.nested_begins == 1
    assert db.nested_commits == 0
    assert db.nested_rollbacks == 1


@pytest.mark.asyncio
async def test_get_for_caller_hides_internal_jobs_from_public_reads():
    db = _FakeDB()
    db.results.append(_ScalarResult(None))

    found = await JobRepo.get_for_caller(db, uuid.uuid4(), "caller-1")

    assert found is None
    sql = _compile(db.statements[0])
    assert "job_aggregates.caller_id =" in sql
    assert "job_aggregates.deleted_at IS NULL" in sql
    assert "job_aggregates.root_job_id IS NULL" in sql
    assert "job_aggregates.workflow_node_key IS NULL" in sql


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
    assert "job_aggregates.root_job_id =" in sql
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
    assert "job_aggregates.workflow_node_key IS NOT NULL" in sql
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
    assert "job_aggregates.root_job_id IS NULL" in sql
    assert "job_aggregates.workflow_node_key IS NULL" in sql
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
    assert "job_aggregates.root_job_id IS NULL" in sql
    assert "job_aggregates.workflow_node_key IS NULL" in sql
    assert "job_aggregates.callback_url IS NOT NULL" in sql
    assert "job_aggregates.deleted_at IS NULL" in sql
    compiled = db.statements[0].compile(dialect=postgresql.dialect())
    assert any(value == "job.succeeded" for value in compiled.params.values())
    assert any(value == "job.failed" for value in compiled.params.values())
    assert "NOT (EXISTS" in sql
    assert "FOR UPDATE SKIP LOCKED" in sql


@pytest.mark.asyncio
async def test_create_child_job_requires_workflow_node_key():
    db = _FakeDB()

    with pytest.raises(ValueError, match="child job must include workflow_node_key"):
        await JobRepo.create(
            db,
            caller_id="caller-1",
            client_request_id=None,
            job_type="example_sleep",
            root_job_id=uuid.uuid4(),
            **_create_kwargs(),
        )


@pytest.mark.asyncio
async def test_create_child_job_rejects_public_submission_identity():
    db = _FakeDB()

    with pytest.raises(ValueError, match="child job must not include client_request_id"):
        await JobRepo.create(
            db,
            caller_id="caller-1",
            client_request_id="request-1",
            job_type="example_sleep",
            root_job_id=uuid.uuid4(),
            workflow_node_key="node.echo",
            **_create_kwargs(),
        )


@pytest.mark.asyncio
async def test_create_child_job_rejects_callback_intent():
    db = _FakeDB()

    with pytest.raises(ValueError, match="child job must not include callback"):
        await JobRepo.create(
            db,
            caller_id="caller-1",
            client_request_id=None,
            job_type="example_sleep",
            root_job_id=uuid.uuid4(),
            workflow_node_key="node.echo",
            callback_url="https://callback.example/jobs",
            **_create_kwargs(),
        )


@pytest.mark.asyncio
async def test_create_public_job_rejects_child_lineage_fields():
    root_job_id = uuid.uuid4()
    db = _FakeDB()

    with pytest.raises(ValueError, match="child job must not include client_request_id"):
        await JobRepo.create(
            db,
            caller_id="caller-1",
            client_request_id="request-1",
            job_type="example_sleep",
            root_job_id=root_job_id,
            workflow_node_key="node.generate-title",
            **_create_kwargs(),
        )

    with pytest.raises(ValueError, match="workflow_node_key requires root_job_id"):
        await JobRepo.create(
            db,
            caller_id="caller-1",
            client_request_id="request-1",
            job_type="example_sleep",
            workflow_node_key="node.generate-title",
            **_create_kwargs(),
        )


@pytest.mark.asyncio
async def test_create_assigns_workflow_lineage_fields():
    root_job_id = uuid.uuid4()
    db = _FakeDB()

    job = await JobRepo.create(
        db,
        caller_id="caller-1",
        client_request_id=None,
        job_type="example_sleep",
        root_job_id=root_job_id,
        workflow_node_key="node.generate-title",
        **_create_kwargs(),
    )

    assert job.root_job_id == root_job_id
    assert job.workflow_node_key == "node.generate-title"
    assert job.client_request_id is None
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
        job_type="example_sleep",
        root_job_id=root_job_id,
        workflow_node_key="node.echo",
        **_create_kwargs(),
    )

    assert job.callback_url is None
    assert job.callback_events is None


@pytest.mark.asyncio
async def test_claim_attempt_for_execution_skips_locked_duplicate_attempt():
    db = _FakeDB()
    db.results.append(_ScalarResult(None))

    claimed = await JobRepo.claim_attempt_for_execution(
        db,
        uuid.uuid4(),
        worker_id="worker-1",
        lease_seconds=60,
    )

    assert claimed is None
    assert len(db.statements) == 1
    sql = _compile(db.statements[0])
    assert "FROM job_aggregates" in sql
    assert "JOIN job_execution_attempts" not in sql
    assert "FOR UPDATE" in sql
    assert "SKIP LOCKED" in sql


@pytest.mark.asyncio
async def test_claim_attempt_for_execution_returns_none_when_attempt_lock_is_busy():
    attempt_id = uuid.uuid4()
    job = Job(
        id=uuid.uuid4(),
        caller_id="caller-1",
        client_request_id="busy-attempt",
        job_type="example_sleep",
        status="queued",
        active_attempt_id=attempt_id,
        progress_percent=0,
        metadata_={},
    )
    db = _FakeDB()
    db.results.extend([_ScalarResult(job), _ScalarResult(None)])

    claimed = await JobRepo.claim_attempt_for_execution(
        db,
        attempt_id,
        worker_id="worker-1",
        lease_seconds=60,
    )

    assert claimed is None
    assert db.flushed is False
    assert len(db.statements) == 2
    job_sql = _compile(db.statements[0])
    attempt_sql = _compile(db.statements[1])
    assert "FROM job_aggregates" in job_sql
    assert "JOIN job_execution_attempts" not in job_sql
    assert "FOR UPDATE" in job_sql
    assert "SKIP LOCKED" in job_sql
    assert "FROM job_execution_attempts" in attempt_sql
    assert "FOR UPDATE" in attempt_sql
    assert "SKIP LOCKED" in attempt_sql


@pytest.mark.asyncio
async def test_claim_attempt_for_execution_accepts_queued_attempt_after_uncertain_publish():
    attempt_id = uuid.uuid4()
    job = Job(
        id=uuid.uuid4(),
        caller_id="caller-1",
        client_request_id="uncertain-publish",
        job_type="example_sleep",
        status="queued",
        active_attempt_id=attempt_id,
        progress_percent=0,
        metadata_={},
    )
    attempt = _attempt(
        id=attempt_id,
        job_id=job.id,
        status="pending",
    )
    db = _FakeDB()
    db.results.extend([_ScalarResult(job), _ScalarResult(attempt)])

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
    assert db.flushed is True
    assert len(db.statements) == 2
    assert "FROM job_aggregates" in _compile(db.statements[0])
    assert "JOIN job_execution_attempts" not in _compile(db.statements[0])
    assert "FROM job_execution_attempts" in _compile(db.statements[1])
    events = [obj for obj in db.added if isinstance(obj, JobEvent)]
    assert len(events) == 1
    assert events[0].event_type == "attempt.claimed"
    assert events[0].from_status == "pending"
    assert events[0].to_status == "running"


@pytest.mark.asyncio
async def test_claim_attempt_for_execution_uses_attempt_timeout_when_longer_than_global_lease():
    attempt_id = uuid.uuid4()
    job = Job(
        id=uuid.uuid4(),
        caller_id="caller-1",
        client_request_id="long-lease-claim",
        job_type="long_audio_job",
        status="queued",
        active_attempt_id=attempt_id,
        progress_percent=0,
        metadata_={},
    )
    attempt = _attempt(
        id=attempt_id,
        job_id=job.id,
        status="pending",
        timeout_seconds=2400,
    )
    db = _FakeDB()
    db.results.extend([_ScalarResult(job), _ScalarResult(attempt)])

    claimed = await JobRepo.claim_attempt_for_execution(
        db,
        attempt_id,
        worker_id="worker-long",
        lease_seconds=900,
    )

    assert claimed is not None
    assert attempt.leased_at is not None
    assert attempt.lease_expires_at is not None
    assert (attempt.lease_expires_at - attempt.leased_at).total_seconds() == 2400


@pytest.mark.asyncio
async def test_claim_attempt_for_execution_keeps_global_lease_floor_when_attempt_timeout_is_shorter():
    attempt_id = uuid.uuid4()
    job = Job(
        id=uuid.uuid4(),
        caller_id="caller-1",
        client_request_id="short-lease-claim",
        job_type="short_job",
        status="queued",
        active_attempt_id=attempt_id,
        progress_percent=0,
        metadata_={},
    )
    attempt = _attempt(
        id=attempt_id,
        job_id=job.id,
        status="pending",
        timeout_seconds=60,
    )
    db = _FakeDB()
    db.results.extend([_ScalarResult(job), _ScalarResult(attempt)])

    claimed = await JobRepo.claim_attempt_for_execution(
        db,
        attempt_id,
        worker_id="worker-short",
        lease_seconds=900,
    )

    assert claimed is not None
    assert attempt.leased_at is not None
    assert attempt.lease_expires_at is not None
    assert (attempt.lease_expires_at - attempt.leased_at).total_seconds() == 900


@pytest.mark.asyncio
async def test_heartbeat_attempt_uses_attempt_timeout_when_longer_than_global_lease():
    lease_token = uuid.uuid4()
    attempt = _attempt(status="running", lease_token=lease_token, timeout_seconds=2400)
    job = Job(
        id=attempt.job_id,
        caller_id="caller-1",
        client_request_id="long-lease-heartbeat",
        job_type="long_audio_job",
        status="running",
        active_attempt_id=attempt.id,
        progress_percent=5,
        metadata_={},
    )
    db = _FakeDB()
    db.results.append(_OneRowResult((job, attempt)))

    updated = await JobRepo.heartbeat_attempt(db, attempt.id, lease_token=lease_token, lease_seconds=900)

    assert updated is True
    assert attempt.heartbeat_at is not None
    assert attempt.lease_expires_at is not None
    assert (attempt.lease_expires_at - attempt.heartbeat_at).total_seconds() == 2400
    assert db.flushed is True


@pytest.mark.asyncio
async def test_heartbeat_attempt_keeps_global_lease_floor_when_attempt_timeout_is_shorter():
    lease_token = uuid.uuid4()
    attempt = _attempt(status="running", lease_token=lease_token, timeout_seconds=60)
    job = Job(
        id=attempt.job_id,
        caller_id="caller-1",
        client_request_id="short-lease-heartbeat",
        job_type="short_job",
        status="running",
        active_attempt_id=attempt.id,
        progress_percent=5,
        metadata_={},
    )
    db = _FakeDB()
    db.results.append(_OneRowResult((job, attempt)))

    updated = await JobRepo.heartbeat_attempt(db, attempt.id, lease_token=lease_token, lease_seconds=900)

    assert updated is True
    assert attempt.heartbeat_at is not None
    assert attempt.lease_expires_at is not None
    assert (attempt.lease_expires_at - attempt.heartbeat_at).total_seconds() == 900
    assert db.flushed is True


@pytest.mark.asyncio
async def test_lease_dispatch_for_publish_claims_due_dispatch_intent():
    attempt_id = uuid.uuid4()
    job = Job(
        id=uuid.uuid4(),
        caller_id="caller-1",
        client_request_id="dispatch-1",
        job_type="example_sleep",
        status="queued",
        active_attempt_id=attempt_id,
        progress_percent=0,
        metadata_={},
    )
    attempt = _attempt(
        id=attempt_id,
        job_id=job.id,
        status="pending",
    )
    dispatch = DispatchOutbox(
        id=uuid.uuid4(),
        event_id=f"job_attempt:{attempt_id}:dispatch",
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
    attempt = _attempt(job_id=uuid.uuid4())
    dispatch = DispatchOutbox(
        id=uuid.uuid4(),
        attempt_id=attempt.id,
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
    db.results.append(_OneRowResult((dispatch, attempt)))

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
    attempt = _attempt(job_id=uuid.uuid4())
    dispatch = DispatchOutbox(
        id=uuid.uuid4(),
        attempt_id=attempt.id,
        event_id="job_attempt:test:dispatch",
        task_name="jobs.run_attempt",
        payload={},
        status="leased",
        lease_token=lease_token,
        publish_attempts=1,
        max_publish_attempts=3,
    )
    deadline = datetime.now(UTC) + timedelta(seconds=30)
    db = _FakeDB()
    db.results.append(_OneRowResult((dispatch, attempt)))

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
async def test_find_dead_lettered_pending_dispatches_requires_active_queued_job():
    db = _FakeDB()
    db.results.append(_ScalarListResult([]))

    await JobRepo.find_dead_lettered_pending_dispatches(db, limit=10)

    sql = _compile(db.statements[0])
    assert "job_aggregates.status =" in sql
    assert "job_aggregates.active_attempt_id = job_execution_attempts.id" in sql
    assert "job_execution_attempts.status =" in sql
    assert "dispatch_outbox.task_name =" in sql
    assert "dispatch_outbox.status =" in sql
    assert "job_aggregates.deleted_at IS NULL" in sql


@pytest.mark.asyncio
async def test_mark_dead_lettered_dispatch_attempt_failed_terminalizes_pending_job(monkeypatch):
    error = {
        "code": "DISPATCH_PUBLISH_EXHAUSTED",
        "message": "任务发布重试已耗尽，已收敛为失败",
    }
    assert get_error_spec("DISPATCH_PUBLISH_EXHAUSTED").retryable is False

    attempt_id = uuid.uuid4()
    job = Job(
        id=uuid.uuid4(),
        caller_id="caller-1",
        client_request_id="dispatch-dead-letter",
        job_type="example_sleep",
        status="queued",
        active_attempt_id=attempt_id,
        progress_percent=0,
        metadata_={},
    )
    attempt = _attempt(id=attempt_id, job_id=job.id, status="pending")
    dispatch = DispatchOutbox(
        id=uuid.uuid4(),
        event_id=f"job_attempt:{attempt_id}:dispatch",
        attempt_id=attempt_id,
        task_name="jobs.run_attempt",
        payload={"attempt_id": str(attempt_id)},
        status="dead_letter",
        dead_lettered_at=datetime.now(UTC),
        last_error={"code": "TASKIQ_PUBLISH_FAILED"},
    )
    db = _FakeDB()
    db.results.append(_OneRowResult((job, attempt, dispatch)))
    callback_jobs = []

    async def ensure_callback(_db, callback_job, *, now):
        callback_jobs.append((callback_job, now))
        return None

    monkeypatch.setattr(JobRepo, "ensure_terminal_callback_outbox", ensure_callback)

    failed_job = await JobRepo.mark_dead_lettered_dispatch_attempt_failed(db, dispatch.id, error=error)

    assert failed_job is job
    assert db.flushed is True
    assert job.status == "failed"
    assert job.active_attempt_id is None
    assert job.error == error
    assert job.progress_stage == "failed"
    assert attempt.status == "failed"
    assert attempt.error == error
    assert attempt.error_kind == "dispatch_error"
    assert attempt.failure_phase == "dispatch"
    assert attempt.retry_eligible is False
    assert attempt.retry_decision == "do_not_retry"
    assert attempt.retry_decision_reason == "dispatch_publish_exhausted"
    assert callback_jobs[0][0] is job
    events = [obj for obj in db.added if isinstance(obj, JobEvent)]
    assert len(events) == 1
    assert events[0].event_type == "attempt.failed"
    assert events[0].from_status == "pending"
    assert events[0].to_status == "failed"
    assert events[0].payload["code"] == "DISPATCH_PUBLISH_EXHAUSTED"
    assert events[0].payload["dispatch_id"] == str(dispatch.id)


@pytest.mark.asyncio
async def test_replay_dead_lettered_dispatch_resets_publish_budget():
    attempt_id = uuid.uuid4()
    job = Job(
        id=uuid.uuid4(),
        caller_id="caller-1",
        client_request_id="dispatch-replay",
        job_type="example_sleep",
        status="queued",
        active_attempt_id=attempt_id,
        progress_percent=0,
        metadata_={},
    )
    attempt = _attempt(id=attempt_id, job_id=job.id, status="pending")
    dispatch = DispatchOutbox(
        id=uuid.uuid4(),
        event_id=f"job_attempt:{attempt_id}:dispatch",
        attempt_id=attempt_id,
        task_name="jobs.run_attempt",
        payload={"attempt_id": str(attempt_id)},
        status="dead_letter",
        publish_attempts=12,
        max_publish_attempts=12,
        next_attempt_at=None,
        published_at=datetime.now(UTC) - timedelta(seconds=3),
        dead_lettered_at=datetime.now(UTC),
        last_error={"code": "TASKIQ_PUBLISH_FAILED"},
    )
    db = _FakeDB()
    db.results.append(_OneRowResult((job, attempt, dispatch)))

    replayed = await JobRepo.replay_dead_lettered_dispatch(
        db,
        job.id,
        reason="manual_dispatch_replay",
        operator="tester",
    )

    assert replayed == (job, attempt, dispatch)
    assert db.flushed is True
    assert job.status == "queued"
    assert attempt.status == "pending"
    assert dispatch.status == "retrying"
    assert dispatch.publish_attempts == 0
    assert dispatch.next_attempt_at is not None
    assert dispatch.lease_token is None
    assert dispatch.lease_expires_at is None
    assert dispatch.leased_at is None
    assert dispatch.published_at is None
    assert dispatch.dead_lettered_at is None
    assert dispatch.last_error is None
    events = [obj for obj in db.added if isinstance(obj, JobEvent)]
    assert len(events) == 1
    assert events[0].event_type == "dispatch.replayed"
    assert events[0].from_status == "dead_letter"
    assert events[0].to_status == "retrying"
    assert events[0].reason == "manual_dispatch_replay"
    assert events[0].payload["operator"] == "tester"
    assert events[0].payload["previous_publish_attempts"] == 12


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
    attempt_id = uuid.uuid4()
    lease_token = uuid.uuid4()
    job = Job(
        id=uuid.uuid4(),
        job_type="test.echo",
        status="running",
        active_attempt_id=attempt_id,
        progress_percent=30,
        metadata_={},
    )
    attempt = _attempt(id=attempt_id, job_id=job.id, status="running", lease_token=lease_token)
    db = _FakeDB()
    db.results.extend([_OneRowResult((job, attempt)), _ScalarResult(None), _ScalarListResult([])])

    updated = await JobRepo.mark_succeeded(
        db,
        job.id,
        attempt_id=attempt_id,
        lease_token=lease_token,
        result={"public": True},
        canonical_result={"canonical": True},
    )

    assert updated is True
    assert db.flushed is True
    assert job.status == "succeeded"
    assert job.active_attempt_id is None
    assert job.result == {"public": True}
    assert job.canonical_result == {"canonical": True}
    assert job.error is None
    assert attempt.status == "succeeded"
    assert attempt.lease_token is None
    assert attempt.lease_expires_at is None


@pytest.mark.asyncio
async def test_mark_succeeded_rejects_stale_attempt_lease():
    attempt_id = uuid.uuid4()
    job = Job(
        id=uuid.uuid4(),
        job_type="test.echo",
        status="running",
        active_attempt_id=attempt_id,
        progress_percent=30,
        metadata_={},
    )
    db = _FakeDB()
    db.results.append(_NoRowResult())

    updated = await JobRepo.mark_succeeded(
        db,
        job.id,
        attempt_id=attempt_id,
        lease_token=uuid.uuid4(),
        result={"public": True},
    )

    assert updated is False
    assert db.flushed is False
    assert job.status == "running"
    assert job.result is None
    sql = _compile(db.statements[0])
    assert "job_execution_attempts.lease_token =" in sql


@pytest.mark.asyncio
async def test_mark_succeeded_creates_pending_callback_outbox_for_subscribed_event():
    attempt_id = uuid.uuid4()
    lease_token = uuid.uuid4()
    job = Job(
        id=uuid.uuid4(),
        caller_id="caller-1",
        client_request_id="client-1",
        job_type="test.echo",
        status="running",
        active_attempt_id=attempt_id,
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
    attempt = _attempt(id=attempt_id, job_id=job.id, status="running", lease_token=lease_token)
    db = _FakeDB()
    db.results.extend([_OneRowResult((job, attempt)), _ScalarResult(None), _ScalarListResult([])])

    updated = await JobRepo.mark_succeeded(
        db,
        job.id,
        attempt_id=attempt_id,
        lease_token=lease_token,
        result={"public": True},
    )

    outboxes = [item for item in db.added if isinstance(item, CallbackOutbox)]
    assert updated is True
    assert job.status == "succeeded"
    assert len(outboxes) == 1
    assert outboxes[0].job_id == job.id
    assert outboxes[0].event_type == "job.succeeded"
    assert outboxes[0].status == "pending"
    assert outboxes[0].next_attempt_at is not None
    assert outboxes[0].payload["event_id"] == str(outboxes[0].event_id)
    assert outboxes[0].payload["trigger_request_id"] == "req-trigger-1"
    assert outboxes[0].payload["job"]["callback"]["status"] == "pending"
    assert outboxes[0].payload["job"]["cost"]["final"] is True
    assert outboxes[0].payload["job"]["usage"] == {
        "ai_call_count": 0,
        "total_tokens": None,
        "final": True,
    }
    assert outboxes[0].payload["job"]["job_progress"]["stage"] == "completed"
    assert outboxes[0].payload["job"]["job_status"] == "succeeded"


@pytest.mark.asyncio
async def test_mark_failed_rejects_stale_attempt_lease():
    error = {"code": "JOB_EXECUTION_FAILED", "message": "failed", "details": {}}
    attempt_id = uuid.uuid4()
    job = Job(
        id=uuid.uuid4(),
        job_type="test.echo",
        status="running",
        active_attempt_id=attempt_id,
        progress_percent=30,
        metadata_={},
    )
    db = _FakeDB()
    db.results.append(_NoRowResult())

    updated = await JobRepo.mark_failed(db, job.id, error, attempt_id=attempt_id, lease_token=uuid.uuid4())

    assert updated is False
    assert db.flushed is False
    assert job.status == "running"
    assert job.error is None
    sql = _compile(db.statements[0])
    assert "job_execution_attempts.lease_token =" in sql


@pytest.mark.asyncio
async def test_mark_failed_creates_skipped_callback_outbox_for_unsubscribed_event(monkeypatch):
    attempt_id = uuid.uuid4()
    lease_token = uuid.uuid4()
    job = Job(
        id=uuid.uuid4(),
        caller_id="caller-1",
        client_request_id="client-1",
        job_type="test.echo",
        status="running",
        active_attempt_id=attempt_id,
        progress_percent=30,
        metadata_={},
        callback_url="https://example.com/callback",
        callback_events=["job.succeeded"],
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    attempt = _attempt(id=attempt_id, job_id=job.id, status="running", lease_token=lease_token)
    db = _FakeDB()
    db.results.extend([_OneRowResult((job, attempt)), _ScalarResult(None), _ScalarListResult([])])
    monkeypatch.setattr(
        "app.jobs.factory.get_job_executor",
        lambda _job_type: _DefaultOffResultSnapshotHandler(),
    )

    updated = await JobRepo.mark_failed(
        db,
        job.id,
        {"code": "JOB_EXECUTION_FAILED", "message": "failed", "details": {}},
        attempt_id=attempt_id,
        lease_token=lease_token,
    )

    outboxes = [item for item in db.added if isinstance(item, CallbackOutbox)]
    assert updated is True
    assert job.status == "failed"
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
    assert outboxes[0].payload["job"]["usage"] == {
        "ai_call_count": 0,
        "total_tokens": None,
        "final": True,
    }


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


@pytest.mark.asyncio
async def test_mark_callback_result_counts_only_actual_http_attempts():
    lease_token = uuid.uuid4()
    job = Job(
        id=uuid.uuid4(),
        caller_id="caller-1",
        client_request_id="client-1",
        job_type="test.echo",
        status="succeeded",
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
        max_delivery_attempts=3,
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
        last_http_status=503,
        last_response={"format": "ack", "valid": False},
        callback_id=outbox.id,
        lease_token=lease_token,
    )

    assert outbox.status == "retrying"
    assert outbox.delivery_attempts == 1
    assert outbox.first_attempt_at is not None
    assert outbox.last_attempt_at is not None
    assert outbox.last_http_status == 503
    assert outbox.last_response == {"format": "ack", "valid": False}


@pytest.mark.asyncio
async def test_mark_callback_result_rejects_retrying_without_next_retry_at():
    lease_token = uuid.uuid4()
    job = Job(
        id=uuid.uuid4(),
        caller_id="caller-1",
        client_request_id="client-1",
        job_type="test.echo",
        status="succeeded",
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
        max_delivery_attempts=3,
    )
    db = _FakeDB()
    db.results.append(_OneRowResult((job, outbox)))

    with pytest.raises(ValueError, match="requires next_retry_at"):
        await JobRepo.mark_callback_result(
            db,
            job.id,
            status="failed",
            last_error={"code": "CALLBACK_HTTP_ERROR"},
            next_retry_at=None,
            max_attempts=3,
            delivery_attempts=1,
            callback_id=outbox.id,
            lease_token=lease_token,
        )

    assert db.flushed is False
    assert outbox.status == "leased"
    assert outbox.delivery_attempts == 0
    assert outbox.lease_token == lease_token
    assert outbox.lease_expires_at is None
    assert outbox.last_error is None
    assert outbox.next_attempt_at is None


@pytest.mark.asyncio
async def test_mark_attempt_failed_closes_attempt_when_job_already_failed():
    error = {"code": "JOB_EXECUTION_FAILED", "message": "failed", "details": {}}
    lease_token = uuid.uuid4()
    job = Job(
        id=uuid.uuid4(),
        job_type="test.echo",
        status="failed",
        active_attempt_id=None,
        progress_percent=30,
        metadata_={},
        error=error,
    )
    attempt = _attempt(
        id=uuid.uuid4(),
        job_id=job.id,
        status="running",
        lease_token=lease_token,
    )
    job.active_attempt_id = attempt.id
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
        progress_percent=15,
        metadata_={},
    )
    attempt = _attempt(
        id=attempt_id,
        job_id=job.id,
        purpose="workflow_orchestration",
        status="running",
        lease_token=lease_token,
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
async def test_count_active_jobs_can_exclude_current_workflow_root():
    db = _FakeDB()
    excluded_job_id = uuid.uuid4()
    db.results.append(_ScalarOneResult(1))

    active_count = await JobRepo.count_active_jobs(db, exclude_job_id=excluded_job_id)

    assert active_count == 1
    sql = _compile(db.statements[0])
    assert "job_aggregates.id !=" in sql
    assert str(excluded_job_id) not in sql


@pytest.mark.asyncio
async def test_mark_workflow_root_succeeded_finalizes_waiting_root_and_callback():
    root = Job(
        id=uuid.uuid4(),
        caller_id="caller-1",
        client_request_id="root-client-1",
        job_type="test.workflow",
        status="running",
        active_attempt_id=None,
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
    assert outboxes[0].payload["job"]["usage"] == {
        "ai_call_count": 0,
        "total_tokens": None,
        "final": True,
    }
    assert events[-1].event_type == "workflow.root.succeeded"
    sql = _compile(db.statements[0])
    assert "job_aggregates.active_attempt_id IS NULL" in sql
    assert "job_aggregates.root_job_id IS NULL" in sql
    assert "job_aggregates.workflow_node_key IS NULL" in sql


@pytest.mark.asyncio
async def test_mark_workflow_root_failed_finalizes_waiting_root_and_callback(monkeypatch):
    root = Job(
        id=uuid.uuid4(),
        caller_id="caller-1",
        client_request_id="root-client-1",
        job_type="test.workflow",
        status="running",
        active_attempt_id=None,
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
    assert outboxes[0].payload["job"]["usage"] == {
        "ai_call_count": 0,
        "total_tokens": None,
        "final": True,
    }
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
    assert "job_aggregates.root_job_id IS NULL" in sql
    assert "job_aggregates.workflow_node_key IS NULL" in sql


@pytest.mark.asyncio
async def test_mark_attempt_failed_creates_retry_attempt_when_allowed():
    error = {"code": "JOB_TIMEOUT", "message": "timed out", "details": {}}
    lease_token = uuid.uuid4()
    job = Job(
        id=uuid.uuid4(),
        job_type="test.echo",
        status="running",
        progress_percent=50,
        metadata_={},
    )
    attempt = _attempt(
        id=uuid.uuid4(),
        job_id=job.id,
        status="running",
        lease_token=lease_token,
        policy_max_attempts=2,
    )
    job.active_attempt_id = attempt.id
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
    assert attempt.retry_eligible is True
    assert attempt.retry_decision == "retry"
    assert job.status == "queued"
    assert job.error is None
    assert len(retry_attempts) == 1
    assert retry_attempts[0].purpose_attempt_no == 2
    assert retry_attempts[0].purpose == "business_execution"
    assert retry_attempts[0].previous_attempt_id == attempt.id
    assert retry_attempts[0].status == "pending"
    assert len(retry_dispatches) == 1
    assert retry_dispatches[0].attempt_id == retry_attempts[0].id
    assert retry_dispatches[0].next_attempt_at == next_attempt_at


@pytest.mark.asyncio
async def test_mark_attempt_failed_uses_attempt_retry_delay_when_no_explicit_schedule():
    error = {"code": "JOB_TIMEOUT", "message": "timed out", "details": {}}
    lease_token = uuid.uuid4()
    job = Job(
        id=uuid.uuid4(),
        job_type="test.echo",
        status="running",
        progress_percent=50,
        metadata_={},
    )
    attempt = _attempt(
        id=uuid.uuid4(),
        job_id=job.id,
        status="running",
        lease_token=lease_token,
        policy_max_attempts=2,
    )
    job.active_attempt_id = attempt.id
    db = _FakeDB()
    db.results.append(_OneRowResult((job, attempt)))

    before = datetime.now(UTC)
    updated = await JobRepo.mark_attempt_failed(
        db,
        attempt.id,
        lease_token=lease_token,
        error=error,
        retryable=True,
    )
    after = datetime.now(UTC)

    retry_dispatches = [item for item in db.added if isinstance(item, DispatchOutbox)]
    assert updated is True
    assert len(retry_dispatches) == 1
    assert attempt.next_attempt_scheduled_at == retry_dispatches[0].next_attempt_at
    assert retry_dispatches[0].next_attempt_at >= before + timedelta(seconds=5)
    assert retry_dispatches[0].next_attempt_at <= after + timedelta(seconds=5)


def test_next_retry_scheduled_at_applies_exponential_backoff():
    now = datetime(2026, 6, 20, 10, 0, tzinfo=UTC)
    attempt = _attempt(
        purpose_attempt_no=3,
        policy_max_attempts=4,
        policy_retry_delay_seconds=5,
        policy_backoff_kind="exponential",
    )

    assert JobRepo._next_retry_scheduled_at(attempt, now) == now + timedelta(seconds=20)


def test_next_retry_scheduled_at_rejects_delayless_fixed_or_exponential_policy():
    now = datetime(2026, 6, 20, 10, 0, tzinfo=UTC)
    attempt = _attempt(
        policy_max_attempts=2,
        policy_backoff_kind="exponential",
    )
    attempt.policy_retry_delay_seconds = None

    with pytest.raises(ValueError, match="requires retry delay seconds"):
        JobRepo._next_retry_scheduled_at(attempt, now)


@pytest.mark.asyncio
async def test_update_progress_can_require_current_task_and_generation():
    attempt_id = uuid.uuid4()
    lease_token = uuid.uuid4()
    job = Job(
        id=uuid.uuid4(),
        job_type="test.echo",
        status="running",
        active_attempt_id=attempt_id,
        progress_percent=10,
    )
    attempt = _attempt(id=attempt_id, job_id=job.id, status="running", lease_token=lease_token)
    db = _FakeDB()
    db.results.append(_OneRowResult((job, attempt)))

    updated = await JobRepo.update_progress(
        db,
        job.id,
        progress_percent=90,
        progress_text="正在执行成功前副作用",
        progress_stage="success_side_effect",
        attempt_id=attempt_id,
        lease_token=lease_token,
    )

    assert updated is True
    assert db.flushed is True
    assert job.progress_percent == 90
    assert job.progress_stage == "success_side_effect"
    sql = _compile(db.statements[0])
    assert "job_aggregates.status =" in sql
    assert "job_execution_attempts.lease_token =" in sql
