"""allow generic job runtime fields

Revision ID: 0006_allow_generic_job_runtime_fields
Revises: 0005_add_job_dispatch_callback_state
Create Date: 2026-06-15
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0006_generic_runtime_fields"
down_revision: Union[str, None] = "0005_dispatch_callback"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "ai_jobs",
        "model_id",
        existing_type=sa.String(length=128),
        nullable=True,
    )


def downgrade() -> None:
    op.execute("UPDATE ai_jobs SET model_id = 'generic-runtime' WHERE model_id IS NULL")
    op.alter_column(
        "ai_jobs",
        "model_id",
        existing_type=sa.String(length=128),
        nullable=False,
    )
