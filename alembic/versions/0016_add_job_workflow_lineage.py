"""add job workflow lineage

Revision ID: 0016_job_workflow_lineage
Revises: 0015_outbox_job_kernel
Create Date: 2026-06-25 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0016_job_workflow_lineage"
down_revision: str | Sequence[str] | None = "0015_outbox_job_kernel"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _drop_constraint_if_exists(table: str, name: str) -> None:
    op.execute(sa.text(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}"))


def upgrade() -> None:
    op.add_column("job_aggregates", sa.Column("root_job_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("job_aggregates", sa.Column("parent_job_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column(
        "job_aggregates",
        sa.Column("is_internal", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column("job_aggregates", sa.Column("workflow_node_key", sa.String(length=255), nullable=True))

    op.create_check_constraint(
        "ck_job_aggregates_internal_root_required",
        "job_aggregates",
        "NOT is_internal OR root_job_id IS NOT NULL",
    )
    op.create_check_constraint(
        "ck_job_aggregates_public_root_self_or_null",
        "job_aggregates",
        "is_internal OR root_job_id IS NULL OR root_job_id = id",
    )
    op.create_check_constraint(
        "ck_job_aggregates_parent_internal",
        "job_aggregates",
        "parent_job_id IS NULL OR is_internal",
    )
    op.create_check_constraint(
        "ck_job_aggregates_node_key_internal",
        "job_aggregates",
        "workflow_node_key IS NULL OR is_internal",
    )
    op.create_check_constraint(
        "ck_job_aggregates_internal_no_client_request",
        "job_aggregates",
        "NOT is_internal OR client_request_id IS NULL",
    )
    op.create_check_constraint(
        "ck_job_aggregates_internal_no_callback",
        "job_aggregates",
        "NOT is_internal OR (callback_url IS NULL AND callback_events IS NULL)",
    )
    op.create_foreign_key(
        "fk_job_aggregates_root_job_id_job_aggregates",
        "job_aggregates",
        "job_aggregates",
        ["root_job_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_job_aggregates_parent_job_id_job_aggregates",
        "job_aggregates",
        "job_aggregates",
        ["parent_job_id"],
        ["id"],
    )
    op.create_index("ix_job_aggregates_root_job_id", "job_aggregates", ["root_job_id"], unique=False)
    op.create_index("ix_job_aggregates_parent_job_id", "job_aggregates", ["parent_job_id"], unique=False)
    op.create_index("ix_job_aggregates_root_status", "job_aggregates", ["root_job_id", "status"], unique=False)
    op.create_index(
        "uq_job_aggregates_root_workflow_node_key",
        "job_aggregates",
        ["root_job_id", "workflow_node_key"],
        unique=True,
        postgresql_where=sa.text("workflow_node_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_job_aggregates_root_workflow_node_key", table_name="job_aggregates")
    op.drop_index("ix_job_aggregates_root_status", table_name="job_aggregates")
    op.drop_index("ix_job_aggregates_parent_job_id", table_name="job_aggregates")
    op.drop_index("ix_job_aggregates_root_job_id", table_name="job_aggregates")
    op.drop_constraint(
        "fk_job_aggregates_parent_job_id_job_aggregates",
        "job_aggregates",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_job_aggregates_root_job_id_job_aggregates",
        "job_aggregates",
        type_="foreignkey",
    )
    _drop_constraint_if_exists("job_aggregates", "ck_job_aggregates_internal_no_callback")
    _drop_constraint_if_exists("job_aggregates", "ck_job_aggregates_internal_no_client_request")
    _drop_constraint_if_exists("job_aggregates", "ck_job_aggregates_node_key_internal")
    _drop_constraint_if_exists("job_aggregates", "ck_job_aggregates_parent_internal")
    _drop_constraint_if_exists("job_aggregates", "ck_job_aggregates_public_root_self_or_null")
    _drop_constraint_if_exists("job_aggregates", "ck_job_aggregates_internal_root_required")
    op.drop_column("job_aggregates", "workflow_node_key")
    op.drop_column("job_aggregates", "is_internal")
    op.drop_column("job_aggregates", "parent_job_id")
    op.drop_column("job_aggregates", "root_job_id")
