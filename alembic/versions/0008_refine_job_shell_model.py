"""refine job shell model

Revision ID: 0008_refine_job_shell_model
Revises: 0007_job_shell_fields
Create Date: 2026-06-17
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0008_refine_job_shell_model"
down_revision: Union[str, None] = "0007_job_shell_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("ai_jobs", "public_metadata", new_column_name="metadata")
    op.alter_column("ai_jobs", "input_ref", new_column_name="job_params_ref")
    op.add_column("ai_jobs", sa.Column("job_params_hash", sa.String(length=128), nullable=True))
    op.alter_column("ai_jobs", "result_payload", new_column_name="result")
    op.add_column("ai_jobs", sa.Column("canonical_result", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.alter_column("ai_jobs", "error_payload", new_column_name="error")

    op.drop_column("ai_jobs", "model_id")
    op.drop_column("ai_jobs", "options_payload")
    op.drop_column("ai_jobs", "runtime_ref")
    op.drop_column("ai_jobs", "prompt_ref")
    op.drop_column("ai_jobs", "output_oss_bucket")
    op.drop_column("ai_jobs", "output_oss_prefix")
    op.drop_column("ai_jobs", "output_oss_region")
    op.drop_column("ai_jobs", "input_payload")
    op.drop_column("ai_jobs", "output_payload")
    op.drop_column("ai_jobs", "callback_payload")
    op.drop_column("ai_jobs", "prompt_payload")
    op.drop_column("ai_jobs", "execution_mode")
    op.drop_column("ai_jobs", "public_result_payload")
    op.drop_column("ai_jobs", "last_execution_error")

    op.drop_column("ai_job_work_items", "input_payload")
    op.alter_column("ai_job_work_items", "result_payload", new_column_name="result")
    op.alter_column("ai_job_work_items", "error_payload", new_column_name="error")


def downgrade() -> None:
    op.alter_column("ai_job_work_items", "error", new_column_name="error_payload")
    op.alter_column("ai_job_work_items", "result", new_column_name="result_payload")
    op.add_column("ai_job_work_items", sa.Column("input_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True))

    op.add_column("ai_jobs", sa.Column("last_execution_error", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("ai_jobs", sa.Column("public_result_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("ai_jobs", sa.Column("execution_mode", sa.String(length=24), nullable=True))
    op.add_column(
        "ai_jobs",
        sa.Column(
            "prompt_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "ai_jobs",
        sa.Column(
            "callback_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "ai_jobs",
        sa.Column(
            "output_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "ai_jobs",
        sa.Column(
            "input_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column("ai_jobs", sa.Column("output_oss_region", sa.String(length=64), nullable=True))
    op.add_column("ai_jobs", sa.Column("output_oss_prefix", sa.String(length=1024), nullable=True))
    op.add_column("ai_jobs", sa.Column("output_oss_bucket", sa.String(length=255), nullable=True))
    op.add_column("ai_jobs", sa.Column("prompt_ref", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("ai_jobs", sa.Column("runtime_ref", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("ai_jobs", sa.Column("options_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("ai_jobs", sa.Column("model_id", sa.String(length=128), nullable=True))

    op.alter_column("ai_jobs", "error", new_column_name="error_payload")
    op.drop_column("ai_jobs", "canonical_result")
    op.alter_column("ai_jobs", "result", new_column_name="result_payload")
    op.drop_column("ai_jobs", "job_params_hash")
    op.alter_column("ai_jobs", "job_params_ref", new_column_name="input_ref")
    op.alter_column("ai_jobs", "metadata", new_column_name="public_metadata")
