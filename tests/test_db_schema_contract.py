from pathlib import Path

from app.core.database import Base
from app.models.job import Job, JobAttempt


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
    }

    assert retired_fields.isdisjoint(Job.__table__.columns.keys())
    assert "dispatch_attempts" in JobAttempt.__table__.columns.keys()


def test_current_orm_excludes_reconciler_leases_table():
    assert "ai_jobs" not in Base.metadata.tables
    assert "ai_job_work_items" not in Base.metadata.tables
    assert "reconciler_leases" not in Base.metadata.tables


def test_current_orm_declares_hardened_job_status_constraints():
    job_status = _constraint_sql(Job.__table__, "ck_jobs_status")
    attempt_status = _constraint_sql(JobAttempt.__table__, "ck_job_attempts_status")

    assert "queued" in job_status
    assert "running" in job_status
    assert "succeeded" in job_status
    assert "failed" in job_status
    assert "canceled" not in job_status

    assert "queued" in attempt_status
    assert "published" in attempt_status
    assert "running" in attempt_status
    assert "succeeded" in attempt_status
    assert "failed" in attempt_status
    assert "timed_out" not in attempt_status
    assert "cancelled" not in attempt_status


def test_cleanup_migration_tightens_legacy_status_constraints():
    migration_0013 = Path("alembic/versions/0013_cleanup_legacy_job_kernel_state.py").read_text()
    migration_0014 = Path("alembic/versions/0014_cleanup_unused_job_shell_fields.py").read_text()

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
