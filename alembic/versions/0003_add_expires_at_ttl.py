"""Add expires_at field for TTL support

Revision ID: 0003_add_expires_at_ttl
Revises: 0002_add_job_workflow
Create Date: 2026-06-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003_add_expires_at_ttl"
down_revision: Union[str, None] = "0002_add_job_workflow"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add expires_at column to ai_jobs table
    op.add_column(
        "ai_jobs",
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now() + interval '24 hours'"),
        ),
    )
    # Create index on expires_at for efficient cleanup queries
    op.create_index("ix_ai_jobs_expires_at", "ai_jobs", ["expires_at"], unique=False)

    # Add expires_at column to ai_job_work_items table
    op.add_column(
        "ai_job_work_items",
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now() + interval '24 hours'"),
        ),
    )
    # Create index on expires_at for efficient cleanup queries
    op.create_index("ix_ai_job_work_items_expires_at", "ai_job_work_items", ["expires_at"], unique=False)


def downgrade() -> None:
    # Drop index and column from ai_job_work_items
    op.drop_index("ix_ai_job_work_items_expires_at", table_name="ai_job_work_items")
    op.drop_column("ai_job_work_items", "expires_at")

    # Drop index and column from ai_jobs
    op.drop_index("ix_ai_jobs_expires_at", table_name="ai_jobs")
    op.drop_column("ai_jobs", "expires_at")
