"""relax ai call ledger job scope constraint

Revision ID: 0017_ai_call_scope_constraint
Revises: 0016_job_workflow_lineage
Create Date: 2026-06-27 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op


revision: str = "0017_ai_call_scope_constraint"
down_revision: str | Sequence[str] | None = "0016_job_workflow_lineage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


CONSTRAINT_NAME = "ck_ai_call_ledger_entries_job_scope_context"
TABLE_NAME = "ai_call_ledger_entries"

RELAXED_JOB_SCOPE_CONTEXT = (
    "scope_type <> 'job' OR ("
    "job_id IS NOT NULL "
    "AND attempt_id IS NOT NULL "
    "AND job_type IS NOT NULL "
    "AND scope_id ~* "
    "'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'"
    ")"
)

STRICT_JOB_SCOPE_CONTEXT = (
    "scope_type <> 'job' OR ("
    "job_id IS NOT NULL "
    "AND scope_id = job_id::text "
    "AND attempt_id IS NOT NULL "
    "AND job_type IS NOT NULL"
    ")"
)


def upgrade() -> None:
    op.drop_constraint(CONSTRAINT_NAME, TABLE_NAME, type_="check")
    op.create_check_constraint(CONSTRAINT_NAME, TABLE_NAME, RELAXED_JOB_SCOPE_CONTEXT)


def downgrade() -> None:
    op.drop_constraint(CONSTRAINT_NAME, TABLE_NAME, type_="check")
    op.create_check_constraint(CONSTRAINT_NAME, TABLE_NAME, STRICT_JOB_SCOPE_CONTEXT)
