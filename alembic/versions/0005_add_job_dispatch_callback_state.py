"""add job dispatch and callback state

Revision ID: 0005_add_job_dispatch_callback_state
Revises: 0004_drop_metadata_payload
Create Date: 2026-06-14
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0005_add_job_dispatch_callback_state"
down_revision: Union[str, None] = "0004_drop_metadata_payload"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("ai_jobs", sa.Column("celery_published_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "ai_jobs",
        sa.Column("callback_status", sa.String(length=24), nullable=False, server_default="pending"),
    )
    op.add_column(
        "ai_jobs",
        sa.Column("callback_attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("ai_jobs", sa.Column("callback_next_retry_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("ai_jobs", sa.Column("callback_last_error", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.execute("UPDATE ai_jobs SET celery_published_at = now() WHERE celery_task_id IS NOT NULL")
    op.execute("UPDATE ai_jobs SET callback_status = 'skipped' WHERE status IN ('succeeded', 'failed', 'canceled')")
    op.create_index("ix_ai_jobs_celery_published_at", "ai_jobs", ["celery_published_at"], unique=False)
    op.create_index("ix_ai_jobs_callback_status", "ai_jobs", ["callback_status"], unique=False)
    op.create_index("ix_ai_jobs_callback_next_retry_at", "ai_jobs", ["callback_next_retry_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_ai_jobs_callback_next_retry_at", table_name="ai_jobs")
    op.drop_index("ix_ai_jobs_callback_status", table_name="ai_jobs")
    op.drop_index("ix_ai_jobs_celery_published_at", table_name="ai_jobs")
    op.drop_column("ai_jobs", "callback_last_error")
    op.drop_column("ai_jobs", "callback_next_retry_at")
    op.drop_column("ai_jobs", "callback_attempts")
    op.drop_column("ai_jobs", "callback_status")
    op.drop_column("ai_jobs", "celery_published_at")
