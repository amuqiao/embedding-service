"""add job workflow internals

Revision ID: 0002_add_job_workflow
Revises: 0001_create_ai_jobs
Create Date: 2026-06-07
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0002_add_job_workflow"
down_revision: Union[str, None] = "0001_create_ai_jobs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("ai_jobs", sa.Column("execution_mode", sa.String(length=24), nullable=True))
    op.add_column("ai_jobs", sa.Column("execution_plan", postgresql.JSONB(astext_type=sa.Text()), nullable=True))

    op.create_table(
        "ai_job_work_items",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("job_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=96), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="queued"),
        sa.Column("celery_task_id", sa.String(length=255), nullable=True),
        sa.Column("input_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("result_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "status IN ('queued','running','succeeded','failed','canceled')",
            name="ck_ai_job_work_items_status",
        ),
        sa.ForeignKeyConstraint(["job_id"], ["ai_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "name", "chunk_index", name="uq_ai_job_work_items_job_name_chunk"),
    )
    op.create_index("ix_ai_job_work_items_job_id", "ai_job_work_items", ["job_id"], unique=False)
    op.create_index("ix_ai_job_work_items_status", "ai_job_work_items", ["status"], unique=False)
    op.create_index("ix_ai_job_work_items_created_at", "ai_job_work_items", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_ai_job_work_items_created_at", table_name="ai_job_work_items")
    op.drop_index("ix_ai_job_work_items_status", table_name="ai_job_work_items")
    op.drop_index("ix_ai_job_work_items_job_id", table_name="ai_job_work_items")
    op.drop_table("ai_job_work_items")
    op.drop_column("ai_jobs", "execution_plan")
    op.drop_column("ai_jobs", "execution_mode")
