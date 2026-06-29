from pathlib import Path

from app.core.database import Base
from app.models.ai_call_log import AiCallLog
from app.models.job import CallbackOutbox, DispatchOutbox, Job, JobAttempt, JobEvent, JobSubmissionKey


def _constraint_sql(table, name: str) -> str:
    for constraint in table.constraints:
        if constraint.name == name:
            return str(constraint.sqltext)
    raise AssertionError(f"constraint not found: {name}")


def _foreign_key_columns(table, name: str) -> tuple[list[str], list[str]]:
    for constraint in table.foreign_key_constraints:
        if constraint.name == name:
            local = [column.name for column in constraint.columns]
            remote = [element.column.table.name + "." + element.column.name for element in constraint.elements]
            return local, remote
    raise AssertionError(f"foreign key not found: {name}")


def test_current_orm_excludes_retired_job_publish_summary_fields():
    retired_fields = {
        "cancel_reason",
        "cancel_requested_at",
        "cancel_requested_by",
        "execution_published_at",
        "execution_plan",
        "dispatch_attempts",
        "first_published_at",
        "last_published_at",
        "idempotency_key",
        "request_fingerprint",
    }

    assert retired_fields.isdisjoint(Job.__table__.columns.keys())
    assert {"published_at", "dispatch_attempts", "next_dispatch_at", "last_dispatch_error"}.isdisjoint(
        JobAttempt.__table__.columns.keys()
    )


def test_current_orm_uses_transactional_outbox_job_kernel_tables():
    assert Job.__tablename__ == "job_aggregates"
    assert JobSubmissionKey.__tablename__ == "job_submission_keys"
    assert JobAttempt.__tablename__ == "job_execution_attempts"
    assert DispatchOutbox.__tablename__ == "dispatch_outbox"
    assert CallbackOutbox.__tablename__ == "callback_outbox"
    assert JobEvent.__tablename__ == "job_audit_events"
    assert AiCallLog.__tablename__ == "ai_call_ledger_entries"


def test_current_orm_excludes_reconciler_leases_table():
    assert "ai_jobs" not in Base.metadata.tables
    assert "ai_job_work_items" not in Base.metadata.tables
    assert "reconciler_leases" not in Base.metadata.tables
    assert "workflow_instances" not in Base.metadata.tables
    assert "workflow_nodes" not in Base.metadata.tables
    assert "workflow_node_dependencies" not in Base.metadata.tables
    assert "workflow_wakeup_outbox" not in Base.metadata.tables


def test_current_orm_declares_job_workflow_lineage_columns_and_indexes():
    columns = Job.__table__.columns
    assert "root_job_id" in columns
    assert "workflow_node_key" in columns
    assert "parent_job_id" not in columns
    assert "is_internal" not in columns
    assert columns["root_job_id"].nullable is True
    assert columns["workflow_node_key"].nullable is True

    indexes = {index.name: index for index in Job.__table__.indexes}
    assert "ix_job_aggregates_root_job_id" in indexes
    assert "ix_job_aggregates_root_status" in indexes
    assert "uq_job_aggregates_root_workflow_node_key" in indexes
    assert indexes["uq_job_aggregates_root_workflow_node_key"].unique is True


def test_current_orm_declares_hardened_job_status_constraints():
    job_status = _constraint_sql(Job.__table__, "ck_job_aggregates_status")
    root_child_shape = _constraint_sql(Job.__table__, "ck_job_aggregates_root_child_shape")
    child_no_callback = _constraint_sql(Job.__table__, "ck_job_aggregates_child_no_callback")
    terminal_no_active_attempt = _constraint_sql(Job.__table__, "ck_job_aggregates_terminal_no_active_attempt")
    progress_range = _constraint_sql(Job.__table__, "ck_job_aggregates_progress_percent_range")
    attempt_status = _constraint_sql(JobAttempt.__table__, "ck_job_execution_attempts_status")
    attempt_purpose = _constraint_sql(JobAttempt.__table__, "ck_job_execution_attempts_purpose")

    assert "queued" in job_status
    assert "running" in job_status
    assert "succeeded" in job_status
    assert "failed" in job_status
    assert "canceled" not in job_status
    assert "root_job_id IS NULL" in root_child_shape
    assert "workflow_node_key IS NULL" in root_child_shape
    assert "client_request_id IS NOT NULL" in root_child_shape
    assert "root_job_id IS NOT NULL" in root_child_shape
    assert "workflow_node_key IS NOT NULL" in root_child_shape
    assert "client_request_id IS NULL" in root_child_shape
    assert "callback_url IS NULL" in child_no_callback
    assert "callback_events IS NULL" in child_no_callback
    assert "active_attempt_id IS NULL" in terminal_no_active_attempt
    assert "progress_percent >= 0" in progress_range

    assert "pending" in attempt_status
    assert "queued" not in attempt_status
    assert "published" not in attempt_status
    assert "running" in attempt_status
    assert "succeeded" in attempt_status
    assert "failed" in attempt_status
    assert "timed_out" not in attempt_status
    assert "cancelled" not in attempt_status
    assert "workflow_orchestration" in attempt_purpose
    assert "business_execution" in attempt_purpose


def test_current_orm_declares_job_kernel_db_invariants():
    local_columns, remote_columns = _foreign_key_columns(Job.__table__, "fk_job_aggregates_active_attempt_same_job")
    assert local_columns == ["id", "active_attempt_id"]
    assert remote_columns == ["job_execution_attempts.job_id", "job_execution_attempts.id"]

    attempt_unique_constraints = {
        constraint.name: [column.name for column in constraint.columns]
        for constraint in JobAttempt.__table__.constraints
        if constraint.name
    }
    assert attempt_unique_constraints["uq_job_execution_attempts_job_id_id"] == ["job_id", "id"]

    dispatch_lease_fields = _constraint_sql(DispatchOutbox.__table__, "ck_dispatch_outbox_lease_fields")
    dispatch_status_fields = _constraint_sql(DispatchOutbox.__table__, "ck_dispatch_outbox_status_fields")
    callback_lease_fields = _constraint_sql(CallbackOutbox.__table__, "ck_callback_outbox_lease_fields")
    callback_status_fields = _constraint_sql(CallbackOutbox.__table__, "ck_callback_outbox_status_fields")

    assert "status = 'leased'" in dispatch_lease_fields
    assert "lease_token IS NOT NULL" in dispatch_lease_fields
    assert "lease_expires_at IS NOT NULL" in dispatch_lease_fields
    assert "leased_at IS NOT NULL" in dispatch_lease_fields
    assert "status != 'leased'" in dispatch_lease_fields
    assert "lease_token IS NULL" in dispatch_lease_fields
    assert "lease_expires_at IS NULL" in dispatch_lease_fields
    assert "status IN ('pending', 'retrying', 'published')" in dispatch_status_fields
    assert "next_attempt_at IS NOT NULL" in dispatch_status_fields
    assert "status = 'dead_letter'" in dispatch_status_fields
    assert "next_attempt_at IS NULL" in dispatch_status_fields
    assert "(status = 'dead_letter') = (dead_lettered_at IS NOT NULL)" in _constraint_sql(
        DispatchOutbox.__table__,
        "ck_dispatch_outbox_dead_lettered_at",
    )
    assert "published_at IS NOT NULL" in _constraint_sql(DispatchOutbox.__table__, "ck_dispatch_outbox_published_at")

    assert "status = 'leased'" in callback_lease_fields
    assert "lease_token IS NOT NULL" in callback_lease_fields
    assert "lease_expires_at IS NOT NULL" in callback_lease_fields
    assert "leased_at IS NOT NULL" in callback_lease_fields
    assert "status != 'leased'" in callback_lease_fields
    assert "lease_token IS NULL" in callback_lease_fields
    assert "lease_expires_at IS NULL" in callback_lease_fields
    assert "status IN ('pending', 'retrying')" in callback_status_fields
    assert "next_attempt_at IS NOT NULL" in callback_status_fields
    assert "status IN ('delivered', 'skipped', 'dead_letter')" in callback_status_fields
    assert "next_attempt_at IS NULL" in callback_status_fields
    assert "(status = 'delivered') = (delivered_at IS NOT NULL)" in _constraint_sql(
        CallbackOutbox.__table__,
        "ck_callback_outbox_delivered_at",
    )
    assert "(status = 'dead_letter') = (dead_lettered_at IS NOT NULL)" in _constraint_sql(
        CallbackOutbox.__table__,
        "ck_callback_outbox_dead_lettered_at",
    )


def test_current_orm_declares_retry_domain_columns_and_excludes_aggregate_retry_state():
    aggregate_columns = Job.__table__.columns.keys()
    assert {
        "timeout_seconds",
        "job_params",
        "result_ref",
        "canonical_result_ref",
        "execution_token",
        "execution_attempts",
        "execution_generation",
        "attempt_count",
        "max_attempts",
        "last_execution_at",
        "last_heartbeat_at",
        "callback_status",
        "callback_attempts",
        "callback_first_attempt_at",
        "callback_last_attempt_at",
        "callback_next_retry_at",
        "callback_delivered_at",
        "callback_failed_at",
        "callback_last_error",
    }.isdisjoint(aggregate_columns)
    assert Job.__table__.columns["job_params_ref"].nullable is False
    assert Job.__table__.columns["job_params_hash"].nullable is False

    attempt_columns = JobAttempt.__table__.columns.keys()
    assert "attempt_no" not in attempt_columns
    assert "retryable" not in attempt_columns
    assert {
        "purpose",
        "purpose_attempt_no",
        "retry_chain_id",
        "previous_attempt_id",
        "created_reason",
        "policy_max_attempts",
        "policy_retry_delay_seconds",
        "policy_backoff_kind",
        "policy_retryable_error_codes",
        "retry_policy_snapshot",
        "retry_eligible",
        "retry_decision",
        "retry_decision_reason",
        "retry_decided_at",
        "next_attempt_scheduled_at",
        "decision_source",
    }.issubset(attempt_columns)

    dispatch_columns = DispatchOutbox.__table__.columns.keys()
    assert "job_id" not in dispatch_columns
    assert {
        "max_publish_attempts",
        "orphan_timeout_seconds",
        "publish_retry_delay_seconds",
        "publish_backoff_kind",
        "publish_retry_policy_snapshot",
    }.issubset(dispatch_columns)

    callback_columns = CallbackOutbox.__table__.columns.keys()
    assert {
        "max_delivery_attempts",
        "request_timeout_seconds",
        "retry_delay_seconds",
        "delivery_retry_policy_snapshot",
    }.issubset(callback_columns)


def test_current_orm_binds_callback_events_none_as_sql_null():
    assert Job.__table__.columns["callback_events"].type.none_as_null is True


def test_cleanup_migration_tightens_legacy_status_constraints():
    migration_0013 = Path("alembic/versions/0013_cleanup_legacy_job_kernel_state.py").read_text()
    migration_0014 = Path("alembic/versions/0014_cleanup_unused_job_shell_fields.py").read_text()
    migration_0015 = Path("alembic/versions/0015_transactional_outbox_job_kernel_tables.py").read_text()
    migration_0016 = Path("alembic/versions/0016_add_job_workflow_lineage.py").read_text()
    migration_0018 = Path("alembic/versions/0018_job_retry_kernel_hardening.py").read_text()
    migration_0019 = Path("alembic/versions/0019_job_kernel_db_invariants.py").read_text()

    assert "cannot tighten jobs.status constraint while legacy statuses exist" in migration_0013
    assert "cannot tighten job_attempts.status constraint while legacy statuses exist" in migration_0013
    assert "ck_jobs_status" in migration_0013
    assert "status IN ('queued', 'running', 'succeeded', 'failed')" in migration_0013
    assert "status IN ('queued', 'published', 'running', 'succeeded', 'failed')" in migration_0013
    assert 'op.drop_table("reconciler_leases")' in migration_0013
    assert 'op.drop_column("jobs", "dispatch_attempts")' in migration_0013

    assert 'op.drop_column("jobs", "execution_plan")' in migration_0014
    assert 'op.drop_column("jobs", "cancel_requested_at")' in migration_0014
    assert 'op.drop_column("jobs", "cancel_requested_by")' in migration_0014
    assert 'op.drop_column("jobs", "cancel_reason")' in migration_0014

    assert 'op.rename_table("jobs", "job_aggregates")' in migration_0015
    assert 'op.rename_table("job_attempts", "job_execution_attempts")' in migration_0015
    assert '"job_submission_keys"' in migration_0015
    assert '"dispatch_outbox"' in migration_0015
    assert 'op.drop_column("job_execution_attempts", "dispatch_attempts")' in migration_0015
    assert migration_0015.index('op.drop_constraint("ck_job_attempts_status"') < migration_0015.index(
        "UPDATE job_execution_attempts SET status = 'pending'"
    )
    assert "SET status = 'published'" in migration_0015
    assert "d.status = 'published'" in migration_0015
    assert migration_0015.index('op.drop_constraint("ck_job_execution_attempts_status"') < migration_0015.index(
        "SET status = 'published'"
    )

    assert 'down_revision: str | Sequence[str] | None = "0015_outbox_job_kernel"' in migration_0016
    assert 'op.add_column("job_aggregates", sa.Column("root_job_id"' in migration_0016
    assert 'op.add_column("job_aggregates", sa.Column("parent_job_id"' in migration_0016
    assert 'op.add_column(\n        "job_aggregates",' in migration_0016
    assert '"is_internal"' in migration_0016
    assert '"workflow_node_key"' in migration_0016
    assert '"ck_job_aggregates_internal_root_required"' in migration_0016
    assert '"ck_job_aggregates_public_root_self_or_null"' in migration_0016
    assert '"ck_job_aggregates_parent_internal"' in migration_0016
    assert '"ck_job_aggregates_node_key_internal"' in migration_0016
    assert '"ck_job_aggregates_internal_no_client_request"' in migration_0016
    assert '"ck_job_aggregates_internal_no_callback"' in migration_0016
    assert '"fk_job_aggregates_root_job_id_job_aggregates"' in migration_0016
    assert '"fk_job_aggregates_parent_job_id_job_aggregates"' in migration_0016
    assert '"ix_job_aggregates_root_job_id"' in migration_0016
    assert '"ix_job_aggregates_parent_job_id"' in migration_0016
    assert '"ix_job_aggregates_root_status"' in migration_0016
    assert '"uq_job_aggregates_root_workflow_node_key"' in migration_0016
    assert "postgresql_where=sa.text(\"workflow_node_key IS NOT NULL\")" in migration_0016
    assert 'down_revision: str | Sequence[str] | None = "0017_ai_call_scope_constraint"' in migration_0018
    assert '"purpose"' in migration_0018
    assert '"purpose_attempt_no"' in migration_0018
    assert '"retry_policy_snapshot"' in migration_0018
    assert '"dispatch_outbox", "job_id"' in migration_0018
    assert '"parent_job_id"' in migration_0018
    assert '"is_internal"' in migration_0018
    assert '"execution_token"' in migration_0018
    assert '"callback_status"' in migration_0018
    assert '"ck_job_aggregates_root_child_shape"' in migration_0018
    assert '"ck_job_execution_attempts_purpose"' in migration_0018

    assert 'down_revision: str | Sequence[str] | None = "0018_retry_kernel_hardening"' in migration_0019
    assert '"uq_job_execution_attempts_job_id_id"' in migration_0019
    assert '"fk_job_aggregates_active_attempt_same_job"' in migration_0019
    assert '"ck_dispatch_outbox_lease_fields"' in migration_0019
    assert '"ck_dispatch_outbox_status_fields"' in migration_0019
    assert '"ck_dispatch_outbox_dead_lettered_at"' in migration_0019
    assert '"ck_dispatch_outbox_published_at"' in migration_0019
    assert '"ck_callback_outbox_lease_fields"' in migration_0019
    assert '"ck_callback_outbox_status_fields"' in migration_0019
    assert '"ck_callback_outbox_delivered_at"' in migration_0019
    assert '"ck_callback_outbox_dead_lettered_at"' in migration_0019
    assert "active_attempt_id must belong to the same job_id" in migration_0019
    assert "UPDATE dispatch_outbox" in migration_0019
    assert "UPDATE callback_outbox" in migration_0019
    assert "SET leased_at = COALESCE(leased_at, updated_at, created_at, now())" in migration_0019
    assert "SET status = 'retrying'" in migration_0019
