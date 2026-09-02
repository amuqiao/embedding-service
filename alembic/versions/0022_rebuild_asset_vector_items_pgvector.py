"""rebuild asset vector items with pgvector

Revision ID: 0022_asset_vector_pgvector
Revises: 0021_add_asset_vector_items
Create Date: 2026-09-02 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0022_asset_vector_pgvector"
down_revision: str | Sequence[str] | None = "0021_add_asset_vector_items"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    # 0021 used a deterministic mock JSONB vector table before launch; rebuild it as the real pgvector table.
    op.drop_table("asset_vector_items")
    op.execute(
        """
        CREATE TABLE asset_vector_items (
            caller_id VARCHAR(64) NOT NULL,
            item_id VARCHAR(255) NOT NULL,
            item_name VARCHAR(512) NOT NULL,
            asset JSONB NOT NULL,
            labels JSONB NOT NULL,
            metadata JSONB NOT NULL,
            embedding vector(768) NOT NULL,
            embedding_text TEXT NOT NULL,
            embedding_model VARCHAR(255) NOT NULL,
            embedding_dimension INTEGER NOT NULL,
            input_sha256 VARCHAR(64) NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            CONSTRAINT pk_asset_vector_items PRIMARY KEY (caller_id, item_id)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_asset_vector_items_embedding_hnsw
        ON asset_vector_items
        USING hnsw (embedding vector_cosine_ops)
        """
    )


def downgrade() -> None:
    op.drop_table("asset_vector_items")
    op.create_table(
        "asset_vector_items",
        sa.Column("caller_id", sa.String(length=64), nullable=False),
        sa.Column("item_id", sa.String(length=255), nullable=False),
        sa.Column("item_name", sa.String(length=512), nullable=False),
        sa.Column("asset", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("labels", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("embedding", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("embedding_text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("caller_id", "item_id", name="pk_asset_vector_items"),
    )
