"""cleanup unused job shell fields

Revision ID: 0014_cleanup_unused_job_shell
Revises: 0013_cleanup_job_kernel
Create Date: 2026-06-23 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0014_cleanup_unused_job_shell"
down_revision: str | Sequence[str] | None = "0013_cleanup_job_kernel"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("jobs", "cancel_reason")
    op.drop_column("jobs", "cancel_requested_by")
    op.drop_column("jobs", "cancel_requested_at")
    op.drop_column("jobs", "execution_plan")


def downgrade() -> None:
    op.add_column("jobs", sa.Column("execution_plan", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("jobs", sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("jobs", sa.Column("cancel_requested_by", sa.String(length=128), nullable=True))
    op.add_column("jobs", sa.Column("cancel_reason", sa.String(length=512), nullable=True))
