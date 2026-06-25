from pathlib import Path

from app.core.database import Base
from app.models.ai_call_log import AiCallLog
from app.models.job import CallbackOutbox, DispatchOutbox, Job, JobAttempt, JobEvent, JobSubmissionKey


def _constraint_sql(table, name: str) -> str:
    for constraint in table.constraints:
        if constraint.name == name:
            return str(constraint.sqltext)
    raise AssertionError(f"constraint not found: {name}")


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
    assert "parent_job_id" in columns
    assert "is_internal" in columns
    assert "workflow_node_key" in columns
    assert columns["root_job_id"].nullable is True
    assert columns["parent_job_id"].nullable is True
    assert columns["is_internal"].nullable is False
    assert columns["workflow_node_key"].nullable is True

    indexes = {index.name: index for index in Job.__table__.indexes}
    assert "ix_job_aggregates_root_job_id" in indexes
    assert "ix_job_aggregates_parent_job_id" in indexes
    assert "ix_job_aggregates_root_status" in indexes
    assert "uq_job_aggregates_root_workflow_node_key" in indexes
    assert indexes["uq_job_aggregates_root_workflow_node_key"].unique is True


def test_current_orm_declares_hardened_job_status_constraints():
    job_status = _constraint_sql(Job.__table__, "ck_job_aggregates_status")
    internal_root_required = _constraint_sql(Job.__table__, "ck_job_aggregates_internal_root_required")
    public_root_self_or_null = _constraint_sql(Job.__table__, "ck_job_aggregates_public_root_self_or_null")
    parent_internal = _constraint_sql(Job.__table__, "ck_job_aggregates_parent_internal")
    node_key_internal = _constraint_sql(Job.__table__, "ck_job_aggregates_node_key_internal")
    internal_no_client_request = _constraint_sql(Job.__table__, "ck_job_aggregates_internal_no_client_request")
    internal_no_callback = _constraint_sql(Job.__table__, "ck_job_aggregates_internal_no_callback")
    attempt_status = _constraint_sql(JobAttempt.__table__, "ck_job_execution_attempts_status")

    assert "queued" in job_status
    assert "running" in job_status
    assert "succeeded" in job_status
    assert "failed" in job_status
    assert "canceled" not in job_status
    assert "root_job_id IS NOT NULL" in internal_root_required
    assert "root_job_id IS NULL" in public_root_self_or_null
    assert "root_job_id = id" in public_root_self_or_null
    assert "parent_job_id IS NULL" in parent_internal
    assert "workflow_node_key IS NULL" in node_key_internal
    assert "client_request_id IS NULL" in internal_no_client_request
    assert "callback_url IS NULL" in internal_no_callback
    assert "callback_events IS NULL" in internal_no_callback

    assert "pending" in attempt_status
    assert "queued" not in attempt_status
    assert "published" not in attempt_status
    assert "running" in attempt_status
    assert "succeeded" in attempt_status
    assert "failed" in attempt_status
    assert "timed_out" not in attempt_status
    assert "cancelled" not in attempt_status


def test_cleanup_migration_tightens_legacy_status_constraints():
    migration_0013 = Path("alembic/versions/0013_cleanup_legacy_job_kernel_state.py").read_text()
    migration_0014 = Path("alembic/versions/0014_cleanup_unused_job_shell_fields.py").read_text()
    migration_0015 = Path("alembic/versions/0015_transactional_outbox_job_kernel_tables.py").read_text()
    migration_0016 = Path("alembic/versions/0016_add_job_workflow_lineage.py").read_text()

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
