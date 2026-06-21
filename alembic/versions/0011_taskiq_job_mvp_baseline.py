"""taskiq job mvp baseline

Revision ID: 0011_taskiq_job_mvp_baseline
Revises: 0010_job_execution_generation
Create Date: 2026-06-21 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0011_taskiq_job_mvp_baseline"
down_revision: str | Sequence[str] | None = "0010_job_execution_generation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM ai_jobs
                WHERE status IN ('queued', 'running')
                  AND deleted_at IS NULL
            ) THEN
                RAISE EXCEPTION 'cannot migrate active ai_jobs to taskiq job model; drain queued/running jobs before upgrade';
            END IF;
        END $$;
        """
    )
    op.rename_table("ai_jobs", "jobs")
    op.add_column("jobs", sa.Column("idempotency_key", sa.String(length=255), nullable=True))
    op.add_column("jobs", sa.Column("execution_token", sa.String(length=255), nullable=True))
    op.add_column("jobs", sa.Column("execution_published_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("jobs", sa.Column("job_params", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")))
    op.add_column("jobs", sa.Column("result_ref", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("jobs", sa.Column("active_attempt_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("jobs", sa.Column("attempt_count", sa.Integer(), nullable=False, server_default=sa.text("0")))
    op.add_column("jobs", sa.Column("max_attempts", sa.Integer(), nullable=False, server_default=sa.text("1")))
    op.add_column("jobs", sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("jobs", sa.Column("cancel_requested_by", sa.String(length=128), nullable=True))
    op.add_column("jobs", sa.Column("cancel_reason", sa.String(length=512), nullable=True))
    op.execute("UPDATE jobs SET idempotency_key = client_request_id WHERE idempotency_key IS NULL")
    op.execute("UPDATE jobs SET callback_events = '[]'::jsonb WHERE callback_events IS NULL")
    op.execute("UPDATE jobs SET callback_status = 'not_configured' WHERE callback_url IS NULL")
    op.create_index("ix_jobs_idempotency_key", "jobs", ["idempotency_key"], unique=False)
    op.create_index("ix_jobs_active_attempt_id", "jobs", ["active_attempt_id"], unique=False)
    op.create_index("ix_jobs_execution_token", "jobs", ["execution_token"], unique=False)
    op.create_check_constraint("ck_jobs_attempt_count_non_negative", "jobs", "attempt_count >= 0")
    op.create_check_constraint("ck_jobs_max_attempts_positive", "jobs", "max_attempts >= 1")

    op.create_table(
        "job_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dispatch_attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("next_dispatch_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_dispatch_error", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("worker_id", sa.String(length=255), nullable=True),
        sa.Column("lease_token", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("leased_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("error", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_kind", sa.String(length=32), nullable=True),
        sa.Column("failure_phase", sa.String(length=32), nullable=True),
        sa.Column("retryable", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("attempt_no >= 1", name="ck_job_attempts_attempt_no_positive"),
        sa.CheckConstraint("dispatch_attempts >= 0", name="ck_job_attempts_dispatch_attempts_non_negative"),
        sa.CheckConstraint("timeout_seconds > 0", name="ck_job_attempts_timeout_positive"),
        sa.CheckConstraint(
            "status IN ('queued', 'published', 'running', 'succeeded', 'failed', 'timed_out', 'cancelled')",
            name="ck_job_attempts_status",
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "attempt_no", name="uq_job_attempts_job_attempt_no"),
    )
    op.create_index("ix_job_attempts_job_id", "job_attempts", ["job_id"], unique=False)
    op.create_index("ix_job_attempts_status", "job_attempts", ["status"], unique=False)
    op.create_index("ix_job_attempts_next_dispatch_at", "job_attempts", ["next_dispatch_at"], unique=False)
    op.create_index("ix_job_attempts_lease_token", "job_attempts", ["lease_token"], unique=False)
    op.create_index("ix_job_attempts_lease_expires_at", "job_attempts", ["lease_expires_at"], unique=False)
    op.create_index("ix_job_attempts_dispatch_due", "job_attempts", ["status", "next_dispatch_at", "created_at"], unique=False)
    op.create_index("ix_job_attempts_running_lease", "job_attempts", ["status", "lease_expires_at"], unique=False)
    op.create_foreign_key(
        "fk_jobs_active_attempt_id_job_attempts",
        "jobs",
        "job_attempts",
        ["active_attempt_id"],
        ["id"],
        deferrable=True,
        initially="DEFERRED",
    )

    op.create_table(
        "callback_outbox",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("delivery_attempt", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_token", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_http_status", sa.Integer(), nullable=True),
        sa.Column("last_error", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("first_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dead_lettered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("delivery_attempt >= 0", name="ck_callback_outbox_delivery_attempt_non_negative"),
        sa.CheckConstraint(
            "status IN ('pending', 'leased', 'delivered', 'failed', 'dead_letter', 'skipped')",
            name="ck_callback_outbox_status",
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "event_type", name="uq_callback_outbox_job_event_type"),
        sa.UniqueConstraint("event_id", name="uq_callback_outbox_event_id"),
    )
    op.create_index("ix_callback_outbox_job_id", "callback_outbox", ["job_id"], unique=False)
    op.create_index("ix_callback_outbox_status", "callback_outbox", ["status"], unique=False)
    op.create_index("ix_callback_outbox_next_attempt_at", "callback_outbox", ["next_attempt_at"], unique=False)
    op.create_index("ix_callback_outbox_lease_token", "callback_outbox", ["lease_token"], unique=False)
    op.create_index("ix_callback_outbox_lease_expires_at", "callback_outbox", ["lease_expires_at"], unique=False)
    op.create_index("ix_callback_outbox_due", "callback_outbox", ["status", "next_attempt_at", "created_at"], unique=False)

    op.create_table(
        "job_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("callback_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("event_type", sa.String(length=96), nullable=False),
        sa.Column("from_status", sa.String(length=24), nullable=True),
        sa.Column("to_status", sa.String(length=24), nullable=True),
        sa.Column("reason", sa.String(length=128), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["attempt_id"], ["job_attempts.id"]),
        sa.ForeignKeyConstraint(["callback_id"], ["callback_outbox.id"]),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_job_events_job_id", "job_events", ["job_id"], unique=False)
    op.create_index("ix_job_events_attempt_id", "job_events", ["attempt_id"], unique=False)
    op.create_index("ix_job_events_callback_id", "job_events", ["callback_id"], unique=False)
    op.create_index("ix_job_events_event_type", "job_events", ["event_type"], unique=False)
    op.create_index("ix_job_events_created_at", "job_events", ["created_at"], unique=False)
    op.create_index("ix_job_events_job_created", "job_events", ["job_id", "created_at"], unique=False)
    op.create_index("ix_job_events_attempt_created", "job_events", ["attempt_id", "created_at"], unique=False)

    op.create_table(
        "reconciler_leases",
        sa.Column("name", sa.String(length=96), nullable=False),
        sa.Column("owner", sa.String(length=255), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("name"),
    )
    op.create_index("ix_reconciler_leases_lease_expires_at", "reconciler_leases", ["lease_expires_at"], unique=False)
    op.drop_table("ai_job_work_items")


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM jobs
                WHERE status IN ('queued', 'running')
                  AND deleted_at IS NULL
            ) THEN
                RAISE EXCEPTION 'cannot downgrade active taskiq jobs; drain queued/running jobs before downgrade';
            END IF;
        END $$;
        """
    )
    op.drop_index("ix_reconciler_leases_lease_expires_at", table_name="reconciler_leases")
    op.drop_table("reconciler_leases")
    op.drop_index("ix_job_events_attempt_created", table_name="job_events")
    op.drop_index("ix_job_events_job_created", table_name="job_events")
    op.drop_index("ix_job_events_created_at", table_name="job_events")
    op.drop_index("ix_job_events_event_type", table_name="job_events")
    op.drop_index("ix_job_events_callback_id", table_name="job_events")
    op.drop_index("ix_job_events_attempt_id", table_name="job_events")
    op.drop_index("ix_job_events_job_id", table_name="job_events")
    op.drop_table("job_events")
    op.drop_index("ix_callback_outbox_due", table_name="callback_outbox")
    op.drop_index("ix_callback_outbox_lease_expires_at", table_name="callback_outbox")
    op.drop_index("ix_callback_outbox_lease_token", table_name="callback_outbox")
    op.drop_index("ix_callback_outbox_next_attempt_at", table_name="callback_outbox")
    op.drop_index("ix_callback_outbox_status", table_name="callback_outbox")
    op.drop_index("ix_callback_outbox_job_id", table_name="callback_outbox")
    op.drop_table("callback_outbox")
    op.drop_index("ix_job_attempts_running_lease", table_name="job_attempts")
    op.drop_index("ix_job_attempts_dispatch_due", table_name="job_attempts")
    op.drop_index("ix_job_attempts_lease_expires_at", table_name="job_attempts")
    op.drop_index("ix_job_attempts_lease_token", table_name="job_attempts")
    op.drop_index("ix_job_attempts_next_dispatch_at", table_name="job_attempts")
    op.drop_index("ix_job_attempts_status", table_name="job_attempts")
    op.drop_index("ix_job_attempts_job_id", table_name="job_attempts")
    op.drop_constraint("fk_jobs_active_attempt_id_job_attempts", "jobs", type_="foreignkey")
    op.drop_table("job_attempts")
    op.drop_constraint("ck_jobs_max_attempts_positive", "jobs", type_="check")
    op.drop_constraint("ck_jobs_attempt_count_non_negative", "jobs", type_="check")
    op.drop_index("ix_jobs_execution_token", table_name="jobs")
    op.drop_index("ix_jobs_active_attempt_id", table_name="jobs")
    op.drop_index("ix_jobs_idempotency_key", table_name="jobs")
    op.drop_column("jobs", "cancel_reason")
    op.drop_column("jobs", "cancel_requested_by")
    op.drop_column("jobs", "cancel_requested_at")
    op.drop_column("jobs", "max_attempts")
    op.drop_column("jobs", "attempt_count")
    op.drop_column("jobs", "active_attempt_id")
    op.drop_column("jobs", "result_ref")
    op.drop_column("jobs", "job_params")
    op.drop_column("jobs", "execution_published_at")
    op.drop_column("jobs", "execution_token")
    op.drop_column("jobs", "idempotency_key")
    op.rename_table("jobs", "ai_jobs")
    op.create_table(
        "ai_job_work_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=96), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("status", sa.String(length=24), nullable=False, server_default=sa.text("'queued'")),
        sa.Column("celery_task_id", sa.String(length=255), nullable=True),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now() + interval '24 hours'"),
        ),
        sa.Column("input_ref", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("result_ref", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_reason", sa.String(length=255), nullable=True),
        sa.Column("execution_generation", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.CheckConstraint(
            "status IN ('queued','running','succeeded','failed','canceled')",
            name="ck_ai_job_work_items_status",
        ),
        sa.ForeignKeyConstraint(["job_id"], ["ai_jobs.id"], name="ai_job_work_items_job_id_fkey"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "job_id",
            "execution_generation",
            "name",
            "chunk_index",
            name="uq_ai_job_work_items_job_generation_name_chunk",
        ),
    )
    op.create_index("ix_ai_job_work_items_job_id", "ai_job_work_items", ["job_id"], unique=False)
    op.create_index("ix_ai_job_work_items_status", "ai_job_work_items", ["status"], unique=False)
    op.create_index("ix_ai_job_work_items_created_at", "ai_job_work_items", ["created_at"], unique=False)
    op.create_index("ix_ai_job_work_items_expires_at", "ai_job_work_items", ["expires_at"], unique=False)
    op.create_index("ix_ai_job_work_items_deleted_at", "ai_job_work_items", ["deleted_at"], unique=False)
    op.create_index(
        "ix_ai_job_work_items_job_generation",
        "ai_job_work_items",
        ["job_id", "execution_generation"],
        unique=False,
    )
