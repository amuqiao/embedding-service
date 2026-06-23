"""transactional outbox job kernel tables

Revision ID: 0015_outbox_job_kernel
Revises: 0014_cleanup_unused_job_shell
Create Date: 2026-06-23 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0015_outbox_job_kernel"
down_revision: str | Sequence[str] | None = "0014_cleanup_unused_job_shell"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _drop_index_if_exists(name: str) -> None:
    op.execute(sa.text(f"DROP INDEX IF EXISTS {name}"))


def _rename_constraint_if_exists(table: str, old_name: str, new_name: str) -> None:
    op.execute(
        sa.text(
            f"""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM pg_constraint
                    WHERE conrelid = '{table}'::regclass
                      AND conname = '{old_name}'
                ) THEN
                    ALTER TABLE {table} RENAME CONSTRAINT {old_name} TO {new_name};
                END IF;
            END $$;
            """
        )
    )


def upgrade() -> None:
    op.rename_table("jobs", "job_aggregates")
    op.rename_table("job_attempts", "job_execution_attempts")
    op.rename_table("job_events", "job_audit_events")
    op.rename_table("ai_call_logs", "ai_call_ledger_entries")

    op.drop_constraint("fk_jobs_active_attempt_id_job_attempts", "job_aggregates", type_="foreignkey")
    op.drop_constraint("ck_jobs_status", "job_aggregates", type_="check")
    op.create_check_constraint(
        "ck_job_aggregates_status",
        "job_aggregates",
        "status IN ('queued', 'running', 'succeeded', 'failed')",
    )
    op.create_foreign_key(
        "fk_job_aggregates_active_attempt_id_job_execution_attempts",
        "job_aggregates",
        "job_execution_attempts",
        ["active_attempt_id"],
        ["id"],
        deferrable=True,
        initially="DEFERRED",
    )

    op.create_table(
        "job_submission_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("caller_id", sa.String(length=64), nullable=False),
        sa.Column("key_kind", sa.String(length=64), nullable=False),
        sa.Column("key_value", sa.String(length=255), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=128), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["job_aggregates.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("caller_id", "key_kind", "key_value", name="uq_job_submission_keys_caller_kind_value"),
    )
    op.create_index("ix_job_submission_keys_job_id", "job_submission_keys", ["job_id"], unique=False)
    op.create_index("ix_job_submission_keys_expires_at", "job_submission_keys", ["expires_at"], unique=False)
    op.create_index("ix_job_submission_keys_created_at", "job_submission_keys", ["created_at"], unique=False)
    op.execute(
        """
        INSERT INTO job_submission_keys (
            id, caller_id, key_kind, key_value, request_fingerprint, job_id, created_at, expires_at
        )
        SELECT
            md5(j.id::text || '-' || j.client_request_id)::uuid,
            j.caller_id,
            'client_request_id',
            j.client_request_id,
            COALESCE(j.request_fingerprint, 'legacy:' || j.id::text),
            j.id,
            COALESCE(j.created_at, now()),
            COALESCE(j.expires_at, COALESCE(j.created_at, now()) + interval '24 hours')
        FROM job_aggregates j
        WHERE j.client_request_id IS NOT NULL
        ON CONFLICT (caller_id, key_kind, key_value) DO NOTHING
        """
    )

    _drop_index_if_exists("ix_jobs_idempotency_key")
    _drop_index_if_exists("ix_ai_jobs_request_fingerprint")
    op.drop_column("job_aggregates", "idempotency_key")
    op.drop_column("job_aggregates", "request_fingerprint")

    op.create_table(
        "dispatch_outbox",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_id", sa.String(length=255), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_name", sa.String(length=128), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("publish_attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_token", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("leased_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dead_lettered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'leased', 'published', 'retrying', 'dead_letter')",
            name="ck_dispatch_outbox_status",
        ),
        sa.CheckConstraint("publish_attempts >= 0", name="ck_dispatch_outbox_publish_attempts_non_negative"),
        sa.ForeignKeyConstraint(["attempt_id"], ["job_execution_attempts.id"]),
        sa.ForeignKeyConstraint(["job_id"], ["job_aggregates.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", name="uq_dispatch_outbox_event_id"),
        sa.UniqueConstraint("attempt_id", "task_name", name="uq_dispatch_outbox_attempt_task"),
    )
    op.create_index("ix_dispatch_outbox_job_id", "dispatch_outbox", ["job_id"], unique=False)
    op.create_index("ix_dispatch_outbox_attempt_id", "dispatch_outbox", ["attempt_id"], unique=False)
    op.create_index("ix_dispatch_outbox_status", "dispatch_outbox", ["status"], unique=False)
    op.create_index("ix_dispatch_outbox_next_attempt_at", "dispatch_outbox", ["next_attempt_at"], unique=False)
    op.create_index("ix_dispatch_outbox_lease_token", "dispatch_outbox", ["lease_token"], unique=False)
    op.create_index("ix_dispatch_outbox_lease_expires_at", "dispatch_outbox", ["lease_expires_at"], unique=False)
    op.create_index("ix_dispatch_outbox_due", "dispatch_outbox", ["status", "next_attempt_at", "created_at"], unique=False)
    op.create_index("ix_dispatch_outbox_lease", "dispatch_outbox", ["status", "lease_expires_at"], unique=False)
    op.execute(
        """
        INSERT INTO dispatch_outbox (
            id, event_id, job_id, attempt_id, task_name, payload, status,
            publish_attempts, next_attempt_at, last_error, created_at, published_at, updated_at
        )
        SELECT
            md5(a.id::text || '-dispatch')::uuid,
            'job_attempt-' || a.id::text || '-dispatch',
            a.job_id,
            a.id,
            'jobs.run_attempt',
            jsonb_build_object('attempt_id', a.id::text),
            CASE
                WHEN a.status IN ('running', 'succeeded', 'failed') THEN 'published'
                WHEN a.status = 'published' THEN 'published'
                WHEN a.last_dispatch_error IS NOT NULL THEN 'retrying'
                ELSE 'pending'
            END,
            COALESCE(a.dispatch_attempts, 0),
            a.next_dispatch_at,
            a.last_dispatch_error,
            COALESCE(a.created_at, now()),
            a.published_at,
            COALESCE(a.updated_at, now())
        FROM job_execution_attempts a
        ON CONFLICT (event_id) DO NOTHING
        """
    )

    op.drop_constraint("ck_job_attempts_status", "job_execution_attempts", type_="check")
    op.execute("UPDATE job_execution_attempts SET status = 'pending' WHERE status IN ('queued', 'published')")
    op.create_check_constraint(
        "ck_job_execution_attempts_status",
        "job_execution_attempts",
        "status IN ('pending', 'running', 'succeeded', 'failed')",
    )
    op.drop_constraint("uq_job_attempts_job_attempt_no", "job_execution_attempts", type_="unique")
    op.create_unique_constraint(
        "uq_job_execution_attempts_job_attempt_no",
        "job_execution_attempts",
        ["job_id", "attempt_no"],
    )
    for index_name in (
        "ix_job_attempts_job_id",
        "ix_job_attempts_status",
        "ix_job_attempts_next_dispatch_at",
        "ix_job_attempts_lease_token",
        "ix_job_attempts_lease_expires_at",
        "ix_job_attempts_dispatch_due",
        "ix_job_attempts_running_lease",
    ):
        _drop_index_if_exists(index_name)
    op.create_index("ix_job_execution_attempts_job_id", "job_execution_attempts", ["job_id"], unique=False)
    op.create_index("ix_job_execution_attempts_status", "job_execution_attempts", ["status"], unique=False)
    op.create_index("ix_job_execution_attempts_lease_token", "job_execution_attempts", ["lease_token"], unique=False)
    op.create_index(
        "ix_job_execution_attempts_lease_expires_at",
        "job_execution_attempts",
        ["lease_expires_at"],
        unique=False,
    )
    op.create_index(
        "ix_job_execution_attempts_running_lease",
        "job_execution_attempts",
        ["status", "lease_expires_at"],
        unique=False,
    )
    op.drop_column("job_execution_attempts", "published_at")
    op.drop_column("job_execution_attempts", "dispatch_attempts")
    op.drop_column("job_execution_attempts", "next_dispatch_at")
    op.drop_column("job_execution_attempts", "last_dispatch_error")

    op.add_column("callback_outbox", sa.Column("callback_url", sa.String(length=2048), nullable=True))
    op.add_column(
        "callback_outbox",
        sa.Column(
            "signature_version",
            sa.String(length=64),
            nullable=False,
            server_default=sa.text("'hmac-sha256:v1'"),
        ),
    )
    op.add_column("callback_outbox", sa.Column("last_response", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("callback_outbox", sa.Column("leased_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(
        """
        UPDATE callback_outbox c
        SET callback_url = j.callback_url
        FROM job_aggregates j
        WHERE j.id = c.job_id
        """
    )
    op.alter_column("callback_outbox", "signature_version", server_default=None)
    op.alter_column("callback_outbox", "delivery_attempt", new_column_name="delivery_attempts")
    op.execute("UPDATE callback_outbox SET status = 'retrying' WHERE status = 'failed'")
    op.drop_constraint("ck_callback_outbox_delivery_attempt_non_negative", "callback_outbox", type_="check")
    op.create_check_constraint(
        "ck_callback_outbox_delivery_attempts_non_negative",
        "callback_outbox",
        "delivery_attempts >= 0",
    )
    op.drop_constraint("ck_callback_outbox_status", "callback_outbox", type_="check")
    op.create_check_constraint(
        "ck_callback_outbox_status",
        "callback_outbox",
        "status IN ('pending', 'leased', 'delivered', 'retrying', 'dead_letter', 'skipped')",
    )

    for index_name in (
        "ix_job_events_job_id",
        "ix_job_events_attempt_id",
        "ix_job_events_callback_id",
        "ix_job_events_event_type",
        "ix_job_events_created_at",
        "ix_job_events_job_created",
        "ix_job_events_attempt_created",
    ):
        _drop_index_if_exists(index_name)
    op.create_index("ix_job_audit_events_job_id", "job_audit_events", ["job_id"], unique=False)
    op.create_index("ix_job_audit_events_attempt_id", "job_audit_events", ["attempt_id"], unique=False)
    op.create_index("ix_job_audit_events_callback_id", "job_audit_events", ["callback_id"], unique=False)
    op.create_index("ix_job_audit_events_event_type", "job_audit_events", ["event_type"], unique=False)
    op.create_index("ix_job_audit_events_created_at", "job_audit_events", ["created_at"], unique=False)
    op.create_index("ix_job_audit_events_job_created", "job_audit_events", ["job_id", "created_at"], unique=False)
    op.create_index(
        "ix_job_audit_events_attempt_created",
        "job_audit_events",
        ["attempt_id", "created_at"],
        unique=False,
    )

    for index_name in (
        "ix_ai_call_logs_created_at",
        "ix_ai_call_logs_scope_created",
        "ix_ai_call_logs_caller_created",
        "ix_ai_call_logs_operation_created",
        "ix_ai_call_logs_job_created",
        "ix_ai_call_logs_attempt_created",
        "ix_ai_call_logs_model_created",
        "ix_ai_call_logs_provider_model_created",
        "ix_ai_call_logs_status_created",
    ):
        _drop_index_if_exists(index_name)
    op.create_index("ix_ai_call_ledger_entries_created_at", "ai_call_ledger_entries", ["created_at"], unique=False)
    op.create_index(
        "ix_ai_call_ledger_entries_scope_created",
        "ai_call_ledger_entries",
        ["scope_type", "scope_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_ai_call_ledger_entries_caller_created",
        "ai_call_ledger_entries",
        ["caller_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_ai_call_ledger_entries_operation_created",
        "ai_call_ledger_entries",
        ["operation", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_ai_call_ledger_entries_job_created",
        "ai_call_ledger_entries",
        ["job_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_ai_call_ledger_entries_attempt_created",
        "ai_call_ledger_entries",
        ["attempt_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_ai_call_ledger_entries_model_created",
        "ai_call_ledger_entries",
        ["model_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_ai_call_ledger_entries_provider_model_created",
        "ai_call_ledger_entries",
        ["provider", "provider_model", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_ai_call_ledger_entries_status_created",
        "ai_call_ledger_entries",
        ["status", "created_at"],
        unique=False,
    )
    _rename_constraint_if_exists("ai_call_ledger_entries", "ck_ai_call_logs_status", "ck_ai_call_ledger_entries_status")
    _rename_constraint_if_exists(
        "ai_call_ledger_entries",
        "ck_ai_call_logs_billable_status",
        "ck_ai_call_ledger_entries_billable_status",
    )
    _rename_constraint_if_exists(
        "ai_call_ledger_entries",
        "ck_ai_call_logs_cost_calculation_status",
        "ck_ai_call_ledger_entries_cost_calculation_status",
    )
    _rename_constraint_if_exists(
        "ai_call_ledger_entries",
        "ck_ai_call_logs_job_scope_context",
        "ck_ai_call_ledger_entries_job_scope_context",
    )
    _rename_constraint_if_exists(
        "ai_call_ledger_entries",
        "ck_ai_call_logs_duration_non_negative",
        "ck_ai_call_ledger_entries_duration_non_negative",
    )
    _rename_constraint_if_exists(
        "ai_call_ledger_entries",
        "ck_ai_call_logs_input_size_non_negative",
        "ck_ai_call_ledger_entries_input_size_non_negative",
    )
    _rename_constraint_if_exists(
        "ai_call_ledger_entries",
        "ck_ai_call_logs_output_size_non_negative",
        "ck_ai_call_ledger_entries_output_size_non_negative",
    )
    _rename_constraint_if_exists(
        "ai_call_ledger_entries",
        "ck_ai_call_logs_cost_non_negative",
        "ck_ai_call_ledger_entries_cost_non_negative",
    )


def downgrade() -> None:
    _rename_constraint_if_exists("ai_call_ledger_entries", "ck_ai_call_ledger_entries_status", "ck_ai_call_logs_status")
    _rename_constraint_if_exists(
        "ai_call_ledger_entries",
        "ck_ai_call_ledger_entries_billable_status",
        "ck_ai_call_logs_billable_status",
    )
    _rename_constraint_if_exists(
        "ai_call_ledger_entries",
        "ck_ai_call_ledger_entries_cost_calculation_status",
        "ck_ai_call_logs_cost_calculation_status",
    )
    _rename_constraint_if_exists(
        "ai_call_ledger_entries",
        "ck_ai_call_ledger_entries_job_scope_context",
        "ck_ai_call_logs_job_scope_context",
    )
    _rename_constraint_if_exists(
        "ai_call_ledger_entries",
        "ck_ai_call_ledger_entries_duration_non_negative",
        "ck_ai_call_logs_duration_non_negative",
    )
    _rename_constraint_if_exists(
        "ai_call_ledger_entries",
        "ck_ai_call_ledger_entries_input_size_non_negative",
        "ck_ai_call_logs_input_size_non_negative",
    )
    _rename_constraint_if_exists(
        "ai_call_ledger_entries",
        "ck_ai_call_ledger_entries_output_size_non_negative",
        "ck_ai_call_logs_output_size_non_negative",
    )
    _rename_constraint_if_exists(
        "ai_call_ledger_entries",
        "ck_ai_call_ledger_entries_cost_non_negative",
        "ck_ai_call_logs_cost_non_negative",
    )
    for index_name in (
        "ix_ai_call_ledger_entries_created_at",
        "ix_ai_call_ledger_entries_scope_created",
        "ix_ai_call_ledger_entries_caller_created",
        "ix_ai_call_ledger_entries_operation_created",
        "ix_ai_call_ledger_entries_job_created",
        "ix_ai_call_ledger_entries_attempt_created",
        "ix_ai_call_ledger_entries_model_created",
        "ix_ai_call_ledger_entries_provider_model_created",
        "ix_ai_call_ledger_entries_status_created",
    ):
        _drop_index_if_exists(index_name)
    op.create_index("ix_ai_call_logs_created_at", "ai_call_ledger_entries", ["created_at"], unique=False)
    op.create_index(
        "ix_ai_call_logs_scope_created",
        "ai_call_ledger_entries",
        ["scope_type", "scope_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_ai_call_logs_caller_created",
        "ai_call_ledger_entries",
        ["caller_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_ai_call_logs_operation_created",
        "ai_call_ledger_entries",
        ["operation", "created_at"],
        unique=False,
    )
    op.create_index("ix_ai_call_logs_job_created", "ai_call_ledger_entries", ["job_id", "created_at"], unique=False)
    op.create_index(
        "ix_ai_call_logs_attempt_created",
        "ai_call_ledger_entries",
        ["attempt_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_ai_call_logs_model_created",
        "ai_call_ledger_entries",
        ["model_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_ai_call_logs_provider_model_created",
        "ai_call_ledger_entries",
        ["provider", "provider_model", "created_at"],
        unique=False,
    )
    op.create_index("ix_ai_call_logs_status_created", "ai_call_ledger_entries", ["status", "created_at"], unique=False)

    for index_name in (
        "ix_job_audit_events_job_id",
        "ix_job_audit_events_attempt_id",
        "ix_job_audit_events_callback_id",
        "ix_job_audit_events_event_type",
        "ix_job_audit_events_created_at",
        "ix_job_audit_events_job_created",
        "ix_job_audit_events_attempt_created",
    ):
        _drop_index_if_exists(index_name)
    op.create_index("ix_job_events_job_id", "job_audit_events", ["job_id"], unique=False)
    op.create_index("ix_job_events_attempt_id", "job_audit_events", ["attempt_id"], unique=False)
    op.create_index("ix_job_events_callback_id", "job_audit_events", ["callback_id"], unique=False)
    op.create_index("ix_job_events_event_type", "job_audit_events", ["event_type"], unique=False)
    op.create_index("ix_job_events_created_at", "job_audit_events", ["created_at"], unique=False)
    op.create_index("ix_job_events_job_created", "job_audit_events", ["job_id", "created_at"], unique=False)
    op.create_index("ix_job_events_attempt_created", "job_audit_events", ["attempt_id", "created_at"], unique=False)

    op.execute("UPDATE callback_outbox SET status = 'failed' WHERE status = 'retrying'")
    op.drop_constraint("ck_callback_outbox_status", "callback_outbox", type_="check")
    op.create_check_constraint(
        "ck_callback_outbox_status",
        "callback_outbox",
        "status IN ('pending', 'leased', 'delivered', 'failed', 'dead_letter', 'skipped')",
    )
    op.drop_constraint("ck_callback_outbox_delivery_attempts_non_negative", "callback_outbox", type_="check")
    op.alter_column("callback_outbox", "delivery_attempts", new_column_name="delivery_attempt")
    op.create_check_constraint(
        "ck_callback_outbox_delivery_attempt_non_negative",
        "callback_outbox",
        "delivery_attempt >= 0",
    )
    op.drop_column("callback_outbox", "leased_at")
    op.drop_column("callback_outbox", "last_response")
    op.drop_column("callback_outbox", "signature_version")
    op.drop_column("callback_outbox", "callback_url")

    op.add_column("job_execution_attempts", sa.Column("last_dispatch_error", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("job_execution_attempts", sa.Column("next_dispatch_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "job_execution_attempts",
        sa.Column("dispatch_attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column("job_execution_attempts", sa.Column("published_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(
        """
        UPDATE job_execution_attempts a
        SET
            published_at = d.published_at,
            dispatch_attempts = COALESCE(d.publish_attempts, 0),
            next_dispatch_at = d.next_attempt_at,
            last_dispatch_error = d.last_error
        FROM dispatch_outbox d
        WHERE d.attempt_id = a.id
        """
    )
    op.drop_constraint("ck_job_execution_attempts_status", "job_execution_attempts", type_="check")
    op.execute(
        """
        UPDATE job_execution_attempts a
        SET status = 'published'
        FROM dispatch_outbox d
        WHERE d.attempt_id = a.id
          AND a.status = 'pending'
          AND d.status = 'published'
        """
    )
    op.execute("UPDATE job_execution_attempts SET status = 'queued' WHERE status = 'pending'")
    op.create_check_constraint(
        "ck_job_attempts_status",
        "job_execution_attempts",
        "status IN ('queued', 'published', 'running', 'succeeded', 'failed')",
    )
    op.drop_constraint("uq_job_execution_attempts_job_attempt_no", "job_execution_attempts", type_="unique")
    op.create_unique_constraint("uq_job_attempts_job_attempt_no", "job_execution_attempts", ["job_id", "attempt_no"])
    for index_name in (
        "ix_job_execution_attempts_job_id",
        "ix_job_execution_attempts_status",
        "ix_job_execution_attempts_lease_token",
        "ix_job_execution_attempts_lease_expires_at",
        "ix_job_execution_attempts_running_lease",
    ):
        _drop_index_if_exists(index_name)
    op.create_index("ix_job_attempts_job_id", "job_execution_attempts", ["job_id"], unique=False)
    op.create_index("ix_job_attempts_status", "job_execution_attempts", ["status"], unique=False)
    op.create_index("ix_job_attempts_next_dispatch_at", "job_execution_attempts", ["next_dispatch_at"], unique=False)
    op.create_index("ix_job_attempts_lease_token", "job_execution_attempts", ["lease_token"], unique=False)
    op.create_index("ix_job_attempts_lease_expires_at", "job_execution_attempts", ["lease_expires_at"], unique=False)
    op.create_index(
        "ix_job_attempts_dispatch_due",
        "job_execution_attempts",
        ["status", "next_dispatch_at", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_job_attempts_running_lease",
        "job_execution_attempts",
        ["status", "lease_expires_at"],
        unique=False,
    )

    for index_name in (
        "ix_dispatch_outbox_lease",
        "ix_dispatch_outbox_due",
        "ix_dispatch_outbox_lease_expires_at",
        "ix_dispatch_outbox_lease_token",
        "ix_dispatch_outbox_next_attempt_at",
        "ix_dispatch_outbox_status",
        "ix_dispatch_outbox_attempt_id",
        "ix_dispatch_outbox_job_id",
    ):
        _drop_index_if_exists(index_name)
    op.drop_table("dispatch_outbox")

    op.add_column("job_aggregates", sa.Column("request_fingerprint", sa.String(length=128), nullable=True))
    op.add_column("job_aggregates", sa.Column("idempotency_key", sa.String(length=255), nullable=True))
    op.execute(
        """
        UPDATE job_aggregates j
        SET
            request_fingerprint = k.request_fingerprint,
            idempotency_key = k.key_value
        FROM job_submission_keys k
        WHERE k.job_id = j.id
          AND k.key_kind = 'client_request_id'
        """
    )
    op.create_index("ix_ai_jobs_request_fingerprint", "job_aggregates", ["request_fingerprint"], unique=False)
    op.create_index("ix_jobs_idempotency_key", "job_aggregates", ["idempotency_key"], unique=False)
    op.drop_index("ix_job_submission_keys_created_at", table_name="job_submission_keys")
    op.drop_index("ix_job_submission_keys_expires_at", table_name="job_submission_keys")
    op.drop_index("ix_job_submission_keys_job_id", table_name="job_submission_keys")
    op.drop_table("job_submission_keys")

    op.drop_constraint(
        "fk_job_aggregates_active_attempt_id_job_execution_attempts",
        "job_aggregates",
        type_="foreignkey",
    )
    op.drop_constraint("ck_job_aggregates_status", "job_aggregates", type_="check")
    op.create_check_constraint(
        "ck_jobs_status",
        "job_aggregates",
        "status IN ('queued', 'running', 'succeeded', 'failed')",
    )
    op.create_foreign_key(
        "fk_jobs_active_attempt_id_job_attempts",
        "job_aggregates",
        "job_execution_attempts",
        ["active_attempt_id"],
        ["id"],
        deferrable=True,
        initially="DEFERRED",
    )

    op.rename_table("ai_call_ledger_entries", "ai_call_logs")
    op.rename_table("job_audit_events", "job_events")
    op.rename_table("job_execution_attempts", "job_attempts")
    op.rename_table("job_aggregates", "jobs")
