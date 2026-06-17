"""add job runtime snapshot ref

Revision ID: 0009_job_runtime_snapshot_ref
Revises: 0008_refine_job_shell_model
Create Date: 2026-06-17
"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy.dialects import postgresql
import sqlalchemy as sa

revision: str = "0009_job_runtime_snapshot_ref"
down_revision: Union[str, None] = "0008_refine_job_shell_model"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("ai_jobs", sa.Column("runtime_ref", postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column("ai_jobs", "runtime_ref")
