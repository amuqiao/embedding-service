"""add job execution generation

Revision ID: 0010_job_execution_generation
Revises: 0009_job_runtime_snapshot_ref
Create Date: 2026-06-18 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0010_job_execution_generation"
down_revision: str | Sequence[str] | None = "0009_job_runtime_snapshot_ref"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ai_jobs",
        sa.Column("execution_generation", sa.Integer(), nullable=False, server_default=sa.text("1")),
    )
    op.add_column(
        "ai_job_work_items",
        sa.Column("execution_generation", sa.Integer(), nullable=False, server_default=sa.text("1")),
    )
    op.drop_constraint(
        "uq_ai_job_work_items_job_name_chunk",
        "ai_job_work_items",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_ai_job_work_items_job_generation_name_chunk",
        "ai_job_work_items",
        ["job_id", "execution_generation", "name", "chunk_index"],
    )
    op.create_index(
        "ix_ai_job_work_items_job_generation",
        "ai_job_work_items",
        ["job_id", "execution_generation"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_ai_job_work_items_job_generation", table_name="ai_job_work_items")
    op.drop_constraint(
        "uq_ai_job_work_items_job_generation_name_chunk",
        "ai_job_work_items",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_ai_job_work_items_job_name_chunk",
        "ai_job_work_items",
        ["job_id", "name", "chunk_index"],
    )
    op.drop_column("ai_job_work_items", "execution_generation")
    op.drop_column("ai_jobs", "execution_generation")
