"""create ai_jobs table

Revision ID: 0001_create_ai_jobs
Revises: 
Create Date: 2026-06-07
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0001_create_ai_jobs"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_jobs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("caller_id", sa.String(length=64), nullable=False, server_default="default"),
        sa.Column("client_request_id", sa.String(length=255), nullable=True),
        sa.Column("job_type", sa.String(length=96), nullable=False),
        sa.Column("model_id", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="queued"),
        sa.Column("progress_percent", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("progress_text", sa.String(length=255), nullable=True),
        sa.Column("input_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("output_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("callback_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("prompt_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("metadata_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("result_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("celery_task_id", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "status IN ('queued','running','succeeded','failed','canceled')",
            name="ck_ai_jobs_status",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_jobs_status", "ai_jobs", ["status"], unique=False)
    op.create_index("ix_ai_jobs_created_at", "ai_jobs", ["created_at"], unique=False)
    op.create_index("ix_ai_jobs_client_request", "ai_jobs", ["caller_id", "client_request_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_ai_jobs_client_request", table_name="ai_jobs")
    op.drop_index("ix_ai_jobs_created_at", table_name="ai_jobs")
    op.drop_index("ix_ai_jobs_status", table_name="ai_jobs")
    op.drop_table("ai_jobs")
