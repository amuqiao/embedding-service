"""add job shell fields and soft delete markers

Revision ID: 0007_job_shell_fields
Revises: 0006_generic_runtime_fields
Create Date: 2026-06-17
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0007_job_shell_fields"
down_revision: Union[str, None] = "0006_generic_runtime_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("ai_jobs", sa.Column("request_fingerprint", sa.String(length=128), nullable=True))
    op.add_column("ai_jobs", sa.Column("progress_stage", sa.String(length=64), nullable=True))
    op.add_column("ai_jobs", sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("ai_jobs", sa.Column("priority", sa.String(length=16), nullable=False, server_default="normal"))
    op.add_column("ai_jobs", sa.Column("timeout_seconds", sa.Integer(), nullable=True))
    op.add_column(
        "ai_jobs",
        sa.Column(
            "public_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column("ai_jobs", sa.Column("options_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("ai_jobs", sa.Column("input_ref", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("ai_jobs", sa.Column("runtime_ref", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("ai_jobs", sa.Column("prompt_ref", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("ai_jobs", sa.Column("output_oss_bucket", sa.String(length=255), nullable=True))
    op.add_column("ai_jobs", sa.Column("output_oss_prefix", sa.String(length=1024), nullable=True))
    op.add_column("ai_jobs", sa.Column("output_oss_region", sa.String(length=64), nullable=True))
    op.add_column("ai_jobs", sa.Column("callback_url", sa.String(length=2048), nullable=True))
    op.add_column("ai_jobs", sa.Column("callback_events", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("ai_jobs", sa.Column("public_result_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("ai_jobs", sa.Column("canonical_result_ref", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("ai_jobs", sa.Column("dispatch_attempts", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("ai_jobs", sa.Column("first_published_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("ai_jobs", sa.Column("last_published_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("ai_jobs", sa.Column("execution_attempts", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("ai_jobs", sa.Column("last_execution_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("ai_jobs", sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("ai_jobs", sa.Column("last_execution_error", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("ai_jobs", sa.Column("callback_first_attempt_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("ai_jobs", sa.Column("callback_last_attempt_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("ai_jobs", sa.Column("callback_delivered_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("ai_jobs", sa.Column("callback_failed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("ai_jobs", sa.Column("delete_requested_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("ai_jobs", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("ai_jobs", sa.Column("deleted_reason", sa.String(length=255), nullable=True))

    op.add_column("ai_job_work_items", sa.Column("input_ref", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("ai_job_work_items", sa.Column("result_ref", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("ai_job_work_items", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("ai_job_work_items", sa.Column("deleted_reason", sa.String(length=255), nullable=True))

    op.execute(
        """
        UPDATE ai_jobs
        SET
          request_fingerprint = input_payload ->> 'request_fingerprint',
          public_metadata = COALESCE(input_payload -> 'metadata', '{}'::jsonb),
          options_payload = input_payload -> 'options',
          priority = COALESCE(NULLIF(input_payload #>> '{options,priority}', ''), 'normal'),
          timeout_seconds = CASE
            WHEN (input_payload #>> '{options,timeout_seconds}') ~ '^[0-9]+$'
            THEN (input_payload #>> '{options,timeout_seconds}')::integer
            ELSE NULL
          END,
          queued_at = created_at,
          output_oss_bucket = output_payload ->> 'oss_bucket',
          output_oss_prefix = output_payload ->> 'oss_prefix',
          output_oss_region = output_payload ->> 'oss_region',
          callback_url = callback_payload ->> 'url',
          callback_events = callback_payload -> 'events',
          first_published_at = celery_published_at,
          last_published_at = celery_published_at,
          last_execution_at = started_at,
          last_heartbeat_at = started_at,
          last_execution_error = error_payload,
          public_result_payload = result_payload,
          callback_delivered_at = CASE WHEN callback_status = 'delivered' THEN finished_at ELSE NULL END,
          callback_failed_at = CASE WHEN callback_status = 'failed' THEN finished_at ELSE NULL END
        """
    )

    op.create_index("ix_ai_jobs_request_fingerprint", "ai_jobs", ["request_fingerprint"], unique=False)
    op.create_index("ix_ai_jobs_deleted_at", "ai_jobs", ["deleted_at"], unique=False)
    op.create_index("ix_ai_jobs_queued_at", "ai_jobs", ["queued_at"], unique=False)
    op.create_index("ix_ai_job_work_items_deleted_at", "ai_job_work_items", ["deleted_at"], unique=False)

    op.drop_constraint("ai_job_work_items_job_id_fkey", "ai_job_work_items", type_="foreignkey")
    op.create_foreign_key(
        "ai_job_work_items_job_id_fkey",
        "ai_job_work_items",
        "ai_jobs",
        ["job_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("ai_job_work_items_job_id_fkey", "ai_job_work_items", type_="foreignkey")
    op.create_foreign_key(
        "ai_job_work_items_job_id_fkey",
        "ai_job_work_items",
        "ai_jobs",
        ["job_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.drop_index("ix_ai_job_work_items_deleted_at", table_name="ai_job_work_items")
    op.drop_index("ix_ai_jobs_queued_at", table_name="ai_jobs")
    op.drop_index("ix_ai_jobs_deleted_at", table_name="ai_jobs")
    op.drop_index("ix_ai_jobs_request_fingerprint", table_name="ai_jobs")

    op.drop_column("ai_job_work_items", "deleted_reason")
    op.drop_column("ai_job_work_items", "deleted_at")
    op.drop_column("ai_job_work_items", "result_ref")
    op.drop_column("ai_job_work_items", "input_ref")

    op.drop_column("ai_jobs", "deleted_reason")
    op.drop_column("ai_jobs", "deleted_at")
    op.drop_column("ai_jobs", "delete_requested_at")
    op.drop_column("ai_jobs", "callback_failed_at")
    op.drop_column("ai_jobs", "callback_delivered_at")
    op.drop_column("ai_jobs", "callback_last_attempt_at")
    op.drop_column("ai_jobs", "callback_first_attempt_at")
    op.drop_column("ai_jobs", "last_execution_error")
    op.drop_column("ai_jobs", "last_heartbeat_at")
    op.drop_column("ai_jobs", "last_execution_at")
    op.drop_column("ai_jobs", "execution_attempts")
    op.drop_column("ai_jobs", "last_published_at")
    op.drop_column("ai_jobs", "first_published_at")
    op.drop_column("ai_jobs", "dispatch_attempts")
    op.drop_column("ai_jobs", "canonical_result_ref")
    op.drop_column("ai_jobs", "public_result_payload")
    op.drop_column("ai_jobs", "callback_events")
    op.drop_column("ai_jobs", "callback_url")
    op.drop_column("ai_jobs", "output_oss_region")
    op.drop_column("ai_jobs", "output_oss_prefix")
    op.drop_column("ai_jobs", "output_oss_bucket")
    op.drop_column("ai_jobs", "prompt_ref")
    op.drop_column("ai_jobs", "runtime_ref")
    op.drop_column("ai_jobs", "input_ref")
    op.drop_column("ai_jobs", "options_payload")
    op.drop_column("ai_jobs", "public_metadata")
    op.drop_column("ai_jobs", "timeout_seconds")
    op.drop_column("ai_jobs", "priority")
    op.drop_column("ai_jobs", "queued_at")
    op.drop_column("ai_jobs", "progress_stage")
    op.drop_column("ai_jobs", "request_fingerprint")
