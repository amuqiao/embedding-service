"""add ai call logs

Revision ID: 0012_add_ai_call_logs
Revises: 0011_taskiq_job_mvp_baseline
Create Date: 2026-06-23 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0012_add_ai_call_logs"
down_revision: str | Sequence[str] | None = "0011_taskiq_job_mvp_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_call_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("caller_id", sa.String(length=64), nullable=False),
        sa.Column("scope_type", sa.String(length=32), nullable=False),
        sa.Column("scope_id", sa.String(length=128), nullable=False),
        sa.Column("operation", sa.String(length=128), nullable=False),
        sa.Column("step_name", sa.String(length=128), nullable=True),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column("trace_id", sa.String(length=128), nullable=True),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("attempt_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("job_type", sa.String(length=96), nullable=True),
        sa.Column("model_id", sa.String(length=128), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("provider_model", sa.String(length=255), nullable=False),
        sa.Column("litellm_model", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("failure_phase", sa.String(length=32), nullable=True),
        sa.Column("error_code", sa.String(length=96), nullable=True),
        sa.Column("error_message", sa.String(length=512), nullable=True),
        sa.Column("request_hash", sa.String(length=128), nullable=True),
        sa.Column("response_hash", sa.String(length=128), nullable=True),
        sa.Column("input_size_bytes", sa.Integer(), nullable=True),
        sa.Column("output_size_bytes", sa.Integer(), nullable=True),
        sa.Column("usage_detail", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("usage_units", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("cost_amount", sa.Numeric(20, 8), nullable=True),
        sa.Column("currency", sa.String(length=8), nullable=True),
        sa.Column("pricing_ref", sa.String(length=255), nullable=True),
        sa.Column("pricing_version", sa.String(length=64), nullable=True),
        sa.Column(
            "cost_calculation_status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("billable_status", sa.String(length=32), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status IN ('pending', 'succeeded', 'failed')", name="ck_ai_call_logs_status"),
        sa.CheckConstraint(
            "billable_status IN ('pending', 'not_billable', 'billable', 'unknown')",
            name="ck_ai_call_logs_billable_status",
        ),
        sa.CheckConstraint(
            "cost_calculation_status IN ('pending', 'estimated', 'failed', 'not_applicable')",
            name="ck_ai_call_logs_cost_calculation_status",
        ),
        sa.CheckConstraint(
            "scope_type <> 'job' OR (job_id IS NOT NULL AND scope_id = job_id::text AND attempt_id IS NOT NULL AND job_type IS NOT NULL)",
            name="ck_ai_call_logs_job_scope_context",
        ),
        sa.CheckConstraint("duration_ms IS NULL OR duration_ms >= 0", name="ck_ai_call_logs_duration_non_negative"),
        sa.CheckConstraint(
            "input_size_bytes IS NULL OR input_size_bytes >= 0",
            name="ck_ai_call_logs_input_size_non_negative",
        ),
        sa.CheckConstraint(
            "output_size_bytes IS NULL OR output_size_bytes >= 0",
            name="ck_ai_call_logs_output_size_non_negative",
        ),
        sa.CheckConstraint("cost_amount IS NULL OR cost_amount >= 0", name="ck_ai_call_logs_cost_non_negative"),
        sa.ForeignKeyConstraint(["attempt_id"], ["job_attempts.id"]),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_call_logs_created_at", "ai_call_logs", ["created_at"], unique=False)
    op.create_index("ix_ai_call_logs_scope_created", "ai_call_logs", ["scope_type", "scope_id", "created_at"], unique=False)
    op.create_index("ix_ai_call_logs_caller_created", "ai_call_logs", ["caller_id", "created_at"], unique=False)
    op.create_index("ix_ai_call_logs_operation_created", "ai_call_logs", ["operation", "created_at"], unique=False)
    op.create_index("ix_ai_call_logs_job_created", "ai_call_logs", ["job_id", "created_at"], unique=False)
    op.create_index("ix_ai_call_logs_attempt_created", "ai_call_logs", ["attempt_id", "created_at"], unique=False)
    op.create_index("ix_ai_call_logs_model_created", "ai_call_logs", ["model_id", "created_at"], unique=False)
    op.create_index(
        "ix_ai_call_logs_provider_model_created",
        "ai_call_logs",
        ["provider", "provider_model", "created_at"],
        unique=False,
    )
    op.create_index("ix_ai_call_logs_status_created", "ai_call_logs", ["status", "created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_ai_call_logs_status_created", table_name="ai_call_logs")
    op.drop_index("ix_ai_call_logs_provider_model_created", table_name="ai_call_logs")
    op.drop_index("ix_ai_call_logs_model_created", table_name="ai_call_logs")
    op.drop_index("ix_ai_call_logs_attempt_created", table_name="ai_call_logs")
    op.drop_index("ix_ai_call_logs_job_created", table_name="ai_call_logs")
    op.drop_index("ix_ai_call_logs_operation_created", table_name="ai_call_logs")
    op.drop_index("ix_ai_call_logs_caller_created", table_name="ai_call_logs")
    op.drop_index("ix_ai_call_logs_scope_created", table_name="ai_call_logs")
    op.drop_index("ix_ai_call_logs_created_at", table_name="ai_call_logs")
    op.drop_table("ai_call_logs")
