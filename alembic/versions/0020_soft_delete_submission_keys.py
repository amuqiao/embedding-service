"""add soft delete state to submission keys

Revision ID: 0020_soft_delete_submission_keys
Revises: 0019_job_kernel_db_invariants
Create Date: 2026-07-02 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0020_soft_delete_submission_keys"
down_revision: str | Sequence[str] | None = "0019_job_kernel_db_invariants"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("job_submission_keys", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("job_submission_keys", sa.Column("deleted_reason", sa.String(length=255), nullable=True))
    op.create_index("ix_job_submission_keys_deleted_at", "job_submission_keys", ["deleted_at"], unique=False)
    op.create_index(
        "uq_job_submission_keys_active_caller_kind_value",
        "job_submission_keys",
        ["caller_id", "key_kind", "key_value"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_constraint(
        "uq_job_submission_keys_caller_kind_value",
        "job_submission_keys",
        type_="unique",
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM job_submission_keys
                    GROUP BY caller_id, key_kind, key_value
                    HAVING count(*) > 1
                ) THEN
                    RAISE EXCEPTION
                        'cannot downgrade job_submission_keys: duplicate caller/key rows exist';
                END IF;
            END $$;
            """
        )
    )
    op.drop_index("uq_job_submission_keys_active_caller_kind_value", table_name="job_submission_keys")
    op.drop_index("ix_job_submission_keys_deleted_at", table_name="job_submission_keys")
    op.create_unique_constraint(
        "uq_job_submission_keys_caller_kind_value",
        "job_submission_keys",
        ["caller_id", "key_kind", "key_value"],
    )
    op.drop_column("job_submission_keys", "deleted_reason")
    op.drop_column("job_submission_keys", "deleted_at")
