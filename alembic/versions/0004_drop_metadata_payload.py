"""drop metadata_payload orphan column from ai_jobs

Revision ID: 0004_drop_metadata_payload
Revises: 0003_add_expires_at_ttl
Create Date: 2026-06-13

metadata_payload was defined in 0001 but never mapped in the ORM.
Dropping it removes the divergence.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_drop_metadata_payload"
down_revision: Union[str, None] = "0003_add_expires_at_ttl"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("ai_jobs", "metadata_payload")


def downgrade() -> None:
    op.add_column(
        "ai_jobs",
        sa.Column("metadata_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
