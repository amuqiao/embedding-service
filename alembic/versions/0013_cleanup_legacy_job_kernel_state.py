"""cleanup legacy job kernel state

Revision ID: 0013_cleanup_job_kernel
Revises: 0012_add_ai_call_logs
Create Date: 2026-06-23 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0013_cleanup_job_kernel"
down_revision: str | Sequence[str] | None = "0012_add_ai_call_logs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM jobs WHERE status NOT IN ('queued', 'running', 'succeeded', 'failed')) THEN
                RAISE EXCEPTION 'cannot tighten jobs.status constraint while legacy statuses exist';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM job_attempts
                WHERE status NOT IN ('queued', 'published', 'running', 'succeeded', 'failed')
            ) THEN
                RAISE EXCEPTION 'cannot tighten job_attempts.status constraint while legacy statuses exist';
            END IF;
        END $$;
        """
    )

    op.drop_constraint("ck_ai_jobs_status", "jobs", type_="check")
    op.create_check_constraint(
        "ck_jobs_status",
        "jobs",
        "status IN ('queued', 'running', 'succeeded', 'failed')",
    )
    op.drop_constraint("ck_job_attempts_status", "job_attempts", type_="check")
    op.create_check_constraint(
        "ck_job_attempts_status",
        "job_attempts",
        "status IN ('queued', 'published', 'running', 'succeeded', 'failed')",
    )

    op.drop_column("jobs", "last_published_at")
    op.drop_column("jobs", "first_published_at")
    op.drop_column("jobs", "dispatch_attempts")
    op.drop_column("jobs", "execution_published_at")

    op.drop_index("ix_reconciler_leases_lease_expires_at", table_name="reconciler_leases")
    op.drop_table("reconciler_leases")


def downgrade() -> None:
    op.create_table(
        "reconciler_leases",
        sa.Column("name", sa.String(length=96), nullable=False),
        sa.Column("owner", sa.String(length=255), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("name"),
    )
    op.create_index("ix_reconciler_leases_lease_expires_at", "reconciler_leases", ["lease_expires_at"], unique=False)

    op.add_column("jobs", sa.Column("execution_published_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("jobs", sa.Column("dispatch_attempts", sa.Integer(), nullable=False, server_default=sa.text("0")))
    op.add_column("jobs", sa.Column("first_published_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("jobs", sa.Column("last_published_at", sa.DateTime(timezone=True), nullable=True))

    op.drop_constraint("ck_job_attempts_status", "job_attempts", type_="check")
    op.create_check_constraint(
        "ck_job_attempts_status",
        "job_attempts",
        "status IN ('queued', 'published', 'running', 'succeeded', 'failed', 'timed_out', 'cancelled')",
    )
    op.drop_constraint("ck_jobs_status", "jobs", type_="check")
    op.create_check_constraint(
        "ck_ai_jobs_status",
        "jobs",
        "status IN ('queued','running','succeeded','failed','canceled')",
    )
