"""add job kernel db invariants

Revision ID: 0019_job_kernel_db_invariants
Revises: 0018_retry_kernel_hardening
Create Date: 2026-06-29 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0019_job_kernel_db_invariants"
down_revision: str | Sequence[str] | None = "0018_retry_kernel_hardening"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _drop_constraint_if_exists(table: str, name: str) -> None:
    op.execute(sa.text(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}"))


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM job_aggregates j
                    LEFT JOIN job_execution_attempts a ON a.id = j.active_attempt_id
                    WHERE j.active_attempt_id IS NOT NULL
                      AND (a.id IS NULL OR a.job_id <> j.id)
                ) THEN
                    RAISE EXCEPTION
                        'cannot harden job_aggregates: active_attempt_id must belong to the same job_id';
                END IF;
            END $$;
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE dispatch_outbox
            SET leased_at = COALESCE(leased_at, updated_at, created_at, now())
            WHERE status = 'leased'
              AND lease_token IS NOT NULL
              AND lease_expires_at IS NOT NULL
              AND leased_at IS NULL
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE dispatch_outbox
            SET status = 'retrying',
                next_attempt_at = COALESCE(lease_expires_at, updated_at, created_at, now()),
                lease_token = NULL,
                lease_expires_at = NULL
            WHERE status = 'leased'
              AND (lease_token IS NULL OR lease_expires_at IS NULL)
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE dispatch_outbox
            SET lease_token = NULL,
                lease_expires_at = NULL
            WHERE status != 'leased'
              AND (lease_token IS NOT NULL OR lease_expires_at IS NOT NULL)
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE dispatch_outbox
            SET next_attempt_at = COALESCE(published_at, updated_at, created_at, now())
            WHERE status IN ('pending', 'retrying', 'published')
              AND next_attempt_at IS NULL
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE dispatch_outbox
            SET dead_lettered_at = COALESCE(updated_at, created_at, now())
            WHERE status = 'dead_letter'
              AND dead_lettered_at IS NULL
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE dispatch_outbox
            SET dead_lettered_at = NULL
            WHERE status != 'dead_letter'
              AND dead_lettered_at IS NOT NULL
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE callback_outbox
            SET leased_at = COALESCE(leased_at, updated_at, created_at, now())
            WHERE status = 'leased'
              AND lease_token IS NOT NULL
              AND lease_expires_at IS NOT NULL
              AND leased_at IS NULL
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE callback_outbox
            SET status = 'retrying',
                next_attempt_at = COALESCE(lease_expires_at, updated_at, created_at, now()),
                lease_token = NULL,
                lease_expires_at = NULL
            WHERE status = 'leased'
              AND (lease_token IS NULL OR lease_expires_at IS NULL)
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE callback_outbox
            SET lease_token = NULL,
                lease_expires_at = NULL
            WHERE status != 'leased'
              AND (lease_token IS NOT NULL OR lease_expires_at IS NOT NULL)
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE callback_outbox
            SET next_attempt_at = COALESCE(updated_at, created_at, now())
            WHERE status IN ('pending', 'retrying')
              AND next_attempt_at IS NULL
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE callback_outbox
            SET next_attempt_at = NULL
            WHERE status IN ('delivered', 'skipped', 'dead_letter')
              AND next_attempt_at IS NOT NULL
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE callback_outbox
            SET delivered_at = COALESCE(last_attempt_at, updated_at, created_at, now())
            WHERE status = 'delivered'
              AND delivered_at IS NULL
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE callback_outbox
            SET delivered_at = NULL
            WHERE status != 'delivered'
              AND delivered_at IS NOT NULL
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE callback_outbox
            SET dead_lettered_at = COALESCE(last_attempt_at, updated_at, created_at, now())
            WHERE status = 'dead_letter'
              AND dead_lettered_at IS NULL
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE callback_outbox
            SET dead_lettered_at = NULL
            WHERE status != 'dead_letter'
              AND dead_lettered_at IS NOT NULL
            """
        )
    )

    op.create_unique_constraint(
        "uq_job_execution_attempts_job_id_id",
        "job_execution_attempts",
        ["job_id", "id"],
    )
    op.create_foreign_key(
        "fk_job_aggregates_active_attempt_same_job",
        "job_aggregates",
        "job_execution_attempts",
        ["id", "active_attempt_id"],
        ["job_id", "id"],
        deferrable=True,
        initially="DEFERRED",
    )

    op.create_check_constraint(
        "ck_dispatch_outbox_lease_fields",
        "dispatch_outbox",
        """
        (
            status = 'leased'
            AND lease_token IS NOT NULL
            AND lease_expires_at IS NOT NULL
            AND leased_at IS NOT NULL
        )
        OR
        (
            status != 'leased'
            AND lease_token IS NULL
            AND lease_expires_at IS NULL
        )
        """,
    )
    op.create_check_constraint(
        "ck_dispatch_outbox_status_fields",
        "dispatch_outbox",
        """
        (
            status IN ('pending', 'retrying', 'published')
            AND next_attempt_at IS NOT NULL
        )
        OR status = 'leased'
        OR (
            status = 'dead_letter'
            AND next_attempt_at IS NULL
        )
        """,
    )
    op.create_check_constraint(
        "ck_dispatch_outbox_dead_lettered_at",
        "dispatch_outbox",
        "(status = 'dead_letter') = (dead_lettered_at IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_dispatch_outbox_published_at",
        "dispatch_outbox",
        "status != 'published' OR published_at IS NOT NULL",
    )

    op.create_check_constraint(
        "ck_callback_outbox_lease_fields",
        "callback_outbox",
        """
        (
            status = 'leased'
            AND lease_token IS NOT NULL
            AND lease_expires_at IS NOT NULL
            AND leased_at IS NOT NULL
        )
        OR
        (
            status != 'leased'
            AND lease_token IS NULL
            AND lease_expires_at IS NULL
        )
        """,
    )
    op.create_check_constraint(
        "ck_callback_outbox_status_fields",
        "callback_outbox",
        """
        (
            status IN ('pending', 'retrying')
            AND next_attempt_at IS NOT NULL
        )
        OR status = 'leased'
        OR (
            status IN ('delivered', 'skipped', 'dead_letter')
            AND next_attempt_at IS NULL
        )
        """,
    )
    op.create_check_constraint(
        "ck_callback_outbox_delivered_at",
        "callback_outbox",
        "(status = 'delivered') = (delivered_at IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_callback_outbox_dead_lettered_at",
        "callback_outbox",
        "(status = 'dead_letter') = (dead_lettered_at IS NOT NULL)",
    )


def downgrade() -> None:
    for constraint_name in (
        "ck_callback_outbox_dead_lettered_at",
        "ck_callback_outbox_delivered_at",
        "ck_callback_outbox_status_fields",
        "ck_callback_outbox_lease_fields",
    ):
        _drop_constraint_if_exists("callback_outbox", constraint_name)
    for constraint_name in (
        "ck_dispatch_outbox_published_at",
        "ck_dispatch_outbox_dead_lettered_at",
        "ck_dispatch_outbox_status_fields",
        "ck_dispatch_outbox_lease_fields",
    ):
        _drop_constraint_if_exists("dispatch_outbox", constraint_name)
    _drop_constraint_if_exists("job_aggregates", "fk_job_aggregates_active_attempt_same_job")
    _drop_constraint_if_exists("job_execution_attempts", "uq_job_execution_attempts_job_id_id")
