"""harden job retry kernel data model

Revision ID: 0018_retry_kernel_hardening
Revises: 0017_ai_call_scope_constraint
Create Date: 2026-06-29 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0018_retry_kernel_hardening"
down_revision: str | Sequence[str] | None = "0017_ai_call_scope_constraint"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _drop_constraint_if_exists(table: str, name: str) -> None:
    op.execute(sa.text(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}"))


def _drop_index_if_exists(name: str) -> None:
    op.execute(sa.text(f"DROP INDEX IF EXISTS {name}"))


def upgrade() -> None:
    op.add_column("job_execution_attempts", sa.Column("purpose", sa.String(length=32), nullable=True))
    op.add_column("job_execution_attempts", sa.Column("purpose_attempt_no", sa.Integer(), nullable=True))
    op.add_column("job_execution_attempts", sa.Column("retry_chain_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column(
        "job_execution_attempts",
        sa.Column("previous_attempt_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "job_execution_attempts",
        sa.Column("created_reason", sa.String(length=64), server_default="initial", nullable=False),
    )
    op.add_column(
        "job_execution_attempts",
        sa.Column("policy_max_attempts", sa.Integer(), server_default=sa.text("1"), nullable=False),
    )
    op.add_column("job_execution_attempts", sa.Column("policy_retry_delay_seconds", sa.Integer(), nullable=True))
    op.add_column(
        "job_execution_attempts",
        sa.Column("policy_backoff_kind", sa.String(length=32), server_default="none", nullable=False),
    )
    op.add_column(
        "job_execution_attempts",
        sa.Column("policy_retryable_error_codes", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "job_execution_attempts",
        sa.Column(
            "retry_policy_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column("job_execution_attempts", sa.Column("retry_eligible", sa.Boolean(), nullable=True))
    op.add_column("job_execution_attempts", sa.Column("retry_decision", sa.String(length=32), nullable=True))
    op.add_column("job_execution_attempts", sa.Column("retry_decision_reason", sa.String(length=255), nullable=True))
    op.add_column("job_execution_attempts", sa.Column("retry_decided_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "job_execution_attempts",
        sa.Column("next_attempt_scheduled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("job_execution_attempts", sa.Column("decision_source", sa.String(length=64), nullable=True))
    op.execute(
        """
        UPDATE job_execution_attempts
        SET purpose = 'business_execution',
            purpose_attempt_no = attempt_no,
            retry_chain_id = id,
            retry_policy_snapshot = jsonb_build_object(
                'domain', 'business_execution',
                'max_attempts', 1,
                'retry_delay_seconds', NULL,
                'backoff_kind', 'none',
                'retryable_error_codes', '[]'::jsonb
            ),
            retry_eligible = retryable
        """
    )
    op.alter_column("job_execution_attempts", "purpose", nullable=False)
    op.alter_column("job_execution_attempts", "purpose_attempt_no", nullable=False)
    op.alter_column("job_execution_attempts", "retry_chain_id", nullable=False)
    _drop_constraint_if_exists("job_execution_attempts", "uq_job_execution_attempts_job_attempt_no")
    _drop_constraint_if_exists("job_execution_attempts", "ck_job_execution_attempts_status")
    op.drop_column("job_execution_attempts", "retryable")
    op.drop_column("job_execution_attempts", "attempt_no")
    op.create_check_constraint(
        "ck_job_execution_attempts_status",
        "job_execution_attempts",
        "status IN ('pending', 'running', 'succeeded', 'failed')",
    )
    op.create_check_constraint(
        "ck_job_execution_attempts_purpose",
        "job_execution_attempts",
        "purpose IN ('workflow_orchestration', 'business_execution')",
    )
    op.create_check_constraint(
        "ck_job_execution_attempts_purpose_attempt_no_positive",
        "job_execution_attempts",
        "purpose_attempt_no >= 1",
    )
    op.create_check_constraint(
        "ck_job_execution_attempts_policy_max_attempts_positive",
        "job_execution_attempts",
        "policy_max_attempts >= 1",
    )
    op.create_check_constraint(
        "ck_job_execution_attempts_policy_retry_delay_non_negative",
        "job_execution_attempts",
        "policy_retry_delay_seconds IS NULL OR policy_retry_delay_seconds >= 0",
    )
    op.create_check_constraint(
        "ck_job_execution_attempts_policy_backoff_kind",
        "job_execution_attempts",
        "policy_backoff_kind IN ('none', 'fixed', 'exponential')",
    )
    op.create_check_constraint(
        "ck_job_execution_attempts_retry_decision",
        "job_execution_attempts",
        "retry_decision IS NULL OR retry_decision IN ('not_decided', 'retry', 'do_not_retry')",
    )
    op.create_unique_constraint(
        "uq_job_execution_attempts_job_purpose_no",
        "job_execution_attempts",
        ["job_id", "purpose", "purpose_attempt_no"],
    )
    op.create_foreign_key(
        "fk_job_execution_attempts_previous_attempt_id",
        "job_execution_attempts",
        "job_execution_attempts",
        ["previous_attempt_id"],
        ["id"],
    )
    op.create_index("ix_job_execution_attempts_retry_chain_id", "job_execution_attempts", ["retry_chain_id"])
    op.create_index(
        "uq_job_execution_attempts_previous_attempt_id",
        "job_execution_attempts",
        ["previous_attempt_id"],
        unique=True,
        postgresql_where=sa.text("previous_attempt_id IS NOT NULL"),
    )

    op.add_column(
        "dispatch_outbox",
        sa.Column("max_publish_attempts", sa.Integer(), server_default=sa.text("12"), nullable=False),
    )
    op.add_column(
        "dispatch_outbox",
        sa.Column("orphan_timeout_seconds", sa.Integer(), server_default=sa.text("300"), nullable=False),
    )
    op.add_column(
        "dispatch_outbox",
        sa.Column("publish_retry_delay_seconds", sa.Integer(), server_default=sa.text("5"), nullable=False),
    )
    op.add_column(
        "dispatch_outbox",
        sa.Column("publish_backoff_kind", sa.String(length=32), server_default="fixed", nullable=False),
    )
    op.add_column(
        "dispatch_outbox",
        sa.Column(
            "publish_retry_policy_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    _drop_index_if_exists("ix_dispatch_outbox_job_id")
    _drop_constraint_if_exists("dispatch_outbox", "dispatch_outbox_job_id_fkey")
    op.drop_column("dispatch_outbox", "job_id")
    op.create_check_constraint(
        "ck_dispatch_outbox_max_publish_attempts_positive",
        "dispatch_outbox",
        "max_publish_attempts >= 1",
    )
    op.create_check_constraint(
        "ck_dispatch_outbox_orphan_timeout_seconds_positive",
        "dispatch_outbox",
        "orphan_timeout_seconds >= 1",
    )
    op.create_check_constraint(
        "ck_dispatch_outbox_publish_retry_delay_seconds_non_negative",
        "dispatch_outbox",
        "publish_retry_delay_seconds >= 0",
    )
    op.create_check_constraint(
        "ck_dispatch_outbox_publish_backoff_kind",
        "dispatch_outbox",
        "publish_backoff_kind IN ('none', 'fixed', 'exponential')",
    )

    op.add_column(
        "callback_outbox",
        sa.Column("max_delivery_attempts", sa.Integer(), server_default=sa.text("12"), nullable=False),
    )
    op.add_column(
        "callback_outbox",
        sa.Column("request_timeout_seconds", sa.Integer(), server_default=sa.text("10"), nullable=False),
    )
    op.add_column(
        "callback_outbox",
        sa.Column("retry_delay_seconds", sa.Integer(), server_default=sa.text("300"), nullable=False),
    )
    op.add_column(
        "callback_outbox",
        sa.Column(
            "delivery_retry_policy_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_callback_outbox_max_delivery_attempts_positive",
        "callback_outbox",
        "max_delivery_attempts >= 1",
    )
    op.create_check_constraint(
        "ck_callback_outbox_request_timeout_seconds_positive",
        "callback_outbox",
        "request_timeout_seconds >= 1",
    )
    op.create_check_constraint(
        "ck_callback_outbox_retry_delay_seconds_non_negative",
        "callback_outbox",
        "retry_delay_seconds >= 0",
    )

    op.execute("UPDATE job_aggregates SET root_job_id = NULL WHERE root_job_id = id")
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM job_aggregates
                WHERE job_params_ref IS NULL OR job_params_hash IS NULL
            ) THEN
                RAISE EXCEPTION 'cannot harden job_aggregates: job_params_ref/job_params_hash contains NULL';
            END IF;
        END $$;
        """
    )
    _drop_index_if_exists("ix_job_aggregates_parent_job_id")
    _drop_index_if_exists("ix_jobs_execution_token")
    _drop_index_if_exists("ix_ai_jobs_callback_status")
    _drop_index_if_exists("ix_ai_jobs_callback_next_retry_at")
    for constraint_name in (
        "ck_job_aggregates_internal_root_required",
        "ck_job_aggregates_public_root_self_or_null",
        "ck_job_aggregates_parent_internal",
        "ck_job_aggregates_node_key_internal",
        "ck_job_aggregates_internal_no_client_request",
        "ck_job_aggregates_internal_no_callback",
        "ck_jobs_max_attempts_positive",
    ):
        _drop_constraint_if_exists("job_aggregates", constraint_name)
    _drop_constraint_if_exists("job_aggregates", "fk_job_aggregates_parent_job_id_job_aggregates")
    op.alter_column("job_aggregates", "job_params_ref", nullable=False)
    op.alter_column("job_aggregates", "job_params_hash", nullable=False)
    for column_name in (
        "parent_job_id",
        "is_internal",
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
    ):
        op.drop_column("job_aggregates", column_name)
    op.create_check_constraint(
        "ck_job_aggregates_priority",
        "job_aggregates",
        "priority IN ('low', 'normal')",
    )
    op.create_check_constraint(
        "ck_job_aggregates_progress_percent_range",
        "job_aggregates",
        "progress_percent >= 0 AND progress_percent <= 100",
    )
    op.create_check_constraint(
        "ck_job_aggregates_root_child_shape",
        "job_aggregates",
        """
        (
            root_job_id IS NULL
            AND workflow_node_key IS NULL
            AND client_request_id IS NOT NULL
        )
        OR
        (
            root_job_id IS NOT NULL
            AND workflow_node_key IS NOT NULL
            AND client_request_id IS NULL
        )
        """,
    )
    op.create_check_constraint(
        "ck_job_aggregates_child_no_callback",
        "job_aggregates",
        "root_job_id IS NULL OR (callback_url IS NULL AND callback_events IS NULL)",
    )
    op.execute("UPDATE job_aggregates SET active_attempt_id = NULL WHERE status IN ('succeeded', 'failed')")
    op.create_check_constraint(
        "ck_job_aggregates_terminal_no_active_attempt",
        "job_aggregates",
        "status NOT IN ('succeeded', 'failed') OR active_attempt_id IS NULL",
    )


def downgrade() -> None:
    for constraint_name in (
        "ck_job_aggregates_terminal_no_active_attempt",
        "ck_job_aggregates_child_no_callback",
        "ck_job_aggregates_root_child_shape",
        "ck_job_aggregates_progress_percent_range",
        "ck_job_aggregates_priority",
    ):
        _drop_constraint_if_exists("job_aggregates", constraint_name)
    op.add_column("job_aggregates", sa.Column("parent_job_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column(
        "job_aggregates",
        sa.Column("is_internal", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column("job_aggregates", sa.Column("timeout_seconds", sa.Integer(), nullable=True))
    op.add_column(
        "job_aggregates",
        sa.Column("job_params", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    )
    op.add_column("job_aggregates", sa.Column("result_ref", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("job_aggregates", sa.Column("canonical_result_ref", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("job_aggregates", sa.Column("execution_token", sa.String(length=255), nullable=True))
    op.add_column(
        "job_aggregates",
        sa.Column("execution_attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column(
        "job_aggregates",
        sa.Column("execution_generation", sa.Integer(), server_default=sa.text("1"), nullable=False),
    )
    op.add_column(
        "job_aggregates",
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column(
        "job_aggregates",
        sa.Column("max_attempts", sa.Integer(), server_default=sa.text("1"), nullable=False),
    )
    op.add_column("job_aggregates", sa.Column("last_execution_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("job_aggregates", sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "job_aggregates",
        sa.Column("callback_status", sa.String(length=24), server_default="pending", nullable=False),
    )
    op.add_column(
        "job_aggregates",
        sa.Column("callback_attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column("job_aggregates", sa.Column("callback_first_attempt_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("job_aggregates", sa.Column("callback_last_attempt_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("job_aggregates", sa.Column("callback_next_retry_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("job_aggregates", sa.Column("callback_delivered_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("job_aggregates", sa.Column("callback_failed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("job_aggregates", sa.Column("callback_last_error", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.execute("UPDATE job_aggregates SET is_internal = true, parent_job_id = root_job_id WHERE root_job_id IS NOT NULL")
    op.create_foreign_key(
        "fk_job_aggregates_parent_job_id_job_aggregates",
        "job_aggregates",
        "job_aggregates",
        ["parent_job_id"],
        ["id"],
    )
    op.create_check_constraint(
        "ck_job_aggregates_internal_root_required",
        "job_aggregates",
        "NOT is_internal OR root_job_id IS NOT NULL",
    )
    op.create_check_constraint(
        "ck_job_aggregates_public_root_self_or_null",
        "job_aggregates",
        "is_internal OR root_job_id IS NULL OR root_job_id = id",
    )
    op.create_check_constraint(
        "ck_job_aggregates_parent_internal",
        "job_aggregates",
        "parent_job_id IS NULL OR is_internal",
    )
    op.create_check_constraint(
        "ck_job_aggregates_node_key_internal",
        "job_aggregates",
        "workflow_node_key IS NULL OR is_internal",
    )
    op.create_check_constraint(
        "ck_job_aggregates_internal_no_client_request",
        "job_aggregates",
        "NOT is_internal OR client_request_id IS NULL",
    )
    op.create_check_constraint(
        "ck_job_aggregates_internal_no_callback",
        "job_aggregates",
        "NOT is_internal OR (callback_url IS NULL AND callback_events IS NULL)",
    )
    op.create_check_constraint("ck_jobs_max_attempts_positive", "job_aggregates", "max_attempts >= 1")
    op.create_index("ix_job_aggregates_parent_job_id", "job_aggregates", ["parent_job_id"])

    for constraint_name in (
        "ck_callback_outbox_retry_delay_seconds_non_negative",
        "ck_callback_outbox_request_timeout_seconds_positive",
        "ck_callback_outbox_max_delivery_attempts_positive",
    ):
        _drop_constraint_if_exists("callback_outbox", constraint_name)
    op.drop_column("callback_outbox", "delivery_retry_policy_snapshot")
    op.drop_column("callback_outbox", "retry_delay_seconds")
    op.drop_column("callback_outbox", "request_timeout_seconds")
    op.drop_column("callback_outbox", "max_delivery_attempts")

    for constraint_name in (
        "ck_dispatch_outbox_publish_backoff_kind",
        "ck_dispatch_outbox_publish_retry_delay_seconds_non_negative",
        "ck_dispatch_outbox_orphan_timeout_seconds_positive",
        "ck_dispatch_outbox_max_publish_attempts_positive",
    ):
        _drop_constraint_if_exists("dispatch_outbox", constraint_name)
    op.add_column("dispatch_outbox", sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.execute(
        """
        UPDATE dispatch_outbox d
        SET job_id = a.job_id
        FROM job_execution_attempts a
        WHERE a.id = d.attempt_id
        """
    )
    op.alter_column("dispatch_outbox", "job_id", nullable=False)
    op.create_foreign_key("dispatch_outbox_job_id_fkey", "dispatch_outbox", "job_aggregates", ["job_id"], ["id"])
    op.create_index("ix_dispatch_outbox_job_id", "dispatch_outbox", ["job_id"])
    op.drop_column("dispatch_outbox", "publish_retry_policy_snapshot")
    op.drop_column("dispatch_outbox", "publish_backoff_kind")
    op.drop_column("dispatch_outbox", "publish_retry_delay_seconds")
    op.drop_column("dispatch_outbox", "orphan_timeout_seconds")
    op.drop_column("dispatch_outbox", "max_publish_attempts")

    op.add_column("job_execution_attempts", sa.Column("attempt_no", sa.Integer(), nullable=True))
    op.add_column("job_execution_attempts", sa.Column("retryable", sa.Boolean(), nullable=True))
    op.execute(
        "UPDATE job_execution_attempts SET attempt_no = purpose_attempt_no, retryable = retry_eligible"
    )
    op.alter_column("job_execution_attempts", "attempt_no", nullable=False)
    _drop_index_if_exists("uq_job_execution_attempts_previous_attempt_id")
    _drop_index_if_exists("ix_job_execution_attempts_retry_chain_id")
    _drop_constraint_if_exists("job_execution_attempts", "fk_job_execution_attempts_previous_attempt_id")
    _drop_constraint_if_exists("job_execution_attempts", "uq_job_execution_attempts_job_purpose_no")
    for constraint_name in (
        "ck_job_execution_attempts_retry_decision",
        "ck_job_execution_attempts_policy_backoff_kind",
        "ck_job_execution_attempts_policy_retry_delay_non_negative",
        "ck_job_execution_attempts_policy_max_attempts_positive",
        "ck_job_execution_attempts_purpose_attempt_no_positive",
        "ck_job_execution_attempts_purpose",
        "ck_job_execution_attempts_status",
    ):
        _drop_constraint_if_exists("job_execution_attempts", constraint_name)
    op.create_check_constraint(
        "ck_job_execution_attempts_status",
        "job_execution_attempts",
        "status IN ('pending', 'running', 'succeeded', 'failed')",
    )
    op.create_unique_constraint(
        "uq_job_execution_attempts_job_attempt_no",
        "job_execution_attempts",
        ["job_id", "attempt_no"],
    )
    for column_name in (
        "decision_source",
        "next_attempt_scheduled_at",
        "retry_decided_at",
        "retry_decision_reason",
        "retry_decision",
        "retry_eligible",
        "retry_policy_snapshot",
        "policy_retryable_error_codes",
        "policy_backoff_kind",
        "policy_retry_delay_seconds",
        "policy_max_attempts",
        "created_reason",
        "previous_attempt_id",
        "retry_chain_id",
        "purpose_attempt_no",
        "purpose",
    ):
        op.drop_column("job_execution_attempts", column_name)
