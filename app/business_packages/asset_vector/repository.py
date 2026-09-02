from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import bindparam, delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.business_packages.asset_vector.models import ASSET_VECTOR_EMBEDDING_DIMENSION, AssetVectorItem
from app.business_packages.asset_vector.schemas import AssetVectorEmbeddedItemResult


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _vector_literal(vector: list[float]) -> str:
    if len(vector) != ASSET_VECTOR_EMBEDDING_DIMENSION:
        raise ValueError(
            f"asset_vector embedding must have {ASSET_VECTOR_EMBEDDING_DIMENSION} dimensions, got {len(vector)}"
        )
    values: list[str] = []
    for value in vector:
        if not math.isfinite(value):
            raise ValueError("asset_vector embedding values must be finite")
        values.append(repr(float(value)))
    return "[" + ",".join(values) + "]"


def _embedding_from_db(value: Any) -> list[float]:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("["):
            decoded = json.loads(stripped)
        else:
            decoded = [part for part in stripped.strip("()").split(",") if part]
    else:
        decoded = value
    if not isinstance(decoded, list):
        raise ValueError("stored embedding must be a vector list")
    vector = [float(item) for item in decoded]
    if len(vector) != ASSET_VECTOR_EMBEDDING_DIMENSION:
        raise ValueError(
            f"stored embedding must have {ASSET_VECTOR_EMBEDDING_DIMENSION} dimensions, got {len(vector)}"
        )
    return vector


async def upsert_embedded_items(
    db: AsyncSession,
    *,
    caller_id: str,
    items: list[AssetVectorEmbeddedItemResult],
) -> list[tuple[str, str]]:
    indexed_at = _now_iso()
    rows = [
        {
            "caller_id": caller_id,
            "item_id": item.item.item_id,
            "item_name": item.item.item_name,
            "asset": json.dumps(item.item.asset.model_dump(exclude_none=True), ensure_ascii=False),
            "labels": json.dumps([label.model_dump(exclude_none=True) for label in item.item.labels], ensure_ascii=False),
            "metadata": json.dumps({} if item.item.metadata is None else item.item.metadata, ensure_ascii=False),
            "embedding": _vector_literal(item.embedding),
            "embedding_text": item.embedding_text,
            "embedding_model": item.model_id,
            "embedding_dimension": item.dimension,
            "input_sha256": item.input_sha256,
        }
        for item in items
    ]
    await db.execute(
        text(
            """
            INSERT INTO asset_vector_items (
                caller_id,
                item_id,
                item_name,
                asset,
                labels,
                metadata,
                embedding,
                embedding_text,
                embedding_model,
                embedding_dimension,
                input_sha256
            )
            VALUES (
                :caller_id,
                :item_id,
                :item_name,
                CAST(:asset AS jsonb),
                CAST(:labels AS jsonb),
                CAST(:metadata AS jsonb),
                CAST(:embedding AS vector),
                :embedding_text,
                :embedding_model,
                :embedding_dimension,
                :input_sha256
            )
            ON CONFLICT (caller_id, item_id) DO UPDATE SET
                item_name = EXCLUDED.item_name,
                asset = EXCLUDED.asset,
                labels = EXCLUDED.labels,
                metadata = EXCLUDED.metadata,
                embedding = EXCLUDED.embedding,
                embedding_text = EXCLUDED.embedding_text,
                embedding_model = EXCLUDED.embedding_model,
                embedding_dimension = EXCLUDED.embedding_dimension,
                input_sha256 = EXCLUDED.input_sha256,
                updated_at = now()
            """
        ),
        rows,
    )
    await db.flush()
    return [(item.item.item_id, indexed_at) for item in items]


async def delete_items(db: AsyncSession, *, caller_id: str, item_ids: list[str]) -> None:
    if item_ids:
        await db.execute(
            delete(AssetVectorItem).where(
                AssetVectorItem.caller_id == caller_id,
                AssetVectorItem.item_id.in_(item_ids),
            )
        )
        await db.flush()


async def existing_item_ids(db: AsyncSession, *, caller_id: str, item_ids: list[str]) -> set[str]:
    if not item_ids:
        return set()
    rows = await db.execute(
        select(AssetVectorItem.item_id).where(
            AssetVectorItem.caller_id == caller_id,
            AssetVectorItem.item_id.in_(item_ids),
        )
    )
    return {str(item_id) for item_id in rows.scalars().all()}


async def list_item_ids(
    db: AsyncSession,
    *,
    caller_id: str,
    limit: int,
    cursor: str | None,
) -> tuple[list[str], str | None]:
    statement = select(AssetVectorItem.item_id).where(AssetVectorItem.caller_id == caller_id)
    if cursor:
        statement = statement.where(AssetVectorItem.item_id > cursor)
    rows = [
        str(item_id)
        for item_id in (
            await db.execute(
                statement.order_by(AssetVectorItem.item_id).limit(limit + 1)
            )
        ).scalars().all()
    ]
    page = rows[:limit]
    next_cursor = page[-1] if len(rows) > limit and page else None
    return page, next_cursor


async def vectors_for_item_ids(
    db: AsyncSession,
    *,
    caller_id: str,
    item_ids: list[str],
) -> dict[str, list[float]]:
    if not item_ids:
        return {}
    statement = (
        text(
            """
            SELECT item_id, embedding::text AS embedding
            FROM asset_vector_items
            WHERE caller_id = :caller_id
              AND item_id IN :item_ids
            """
        )
        .bindparams(bindparam("item_ids", expanding=True))
    )
    rows = await db.execute(statement, {"caller_id": caller_id, "item_ids": item_ids})
    return {str(row.item_id): _embedding_from_db(row.embedding) for row in rows}


async def search_by_vector(
    db: AsyncSession,
    *,
    caller_id: str,
    query_vector: list[float],
    top_k: int,
    candidate_item_ids: list[str] | None,
) -> list[str]:
    if candidate_item_ids is not None and not candidate_item_ids:
        return []
    query = _vector_literal(query_vector)
    if candidate_item_ids is None:
        statement = text(
            """
            SELECT item_id
            FROM asset_vector_items
            WHERE caller_id = :caller_id
            ORDER BY embedding <=> CAST(:query_vector AS vector), item_id ASC
            LIMIT :top_k
            """
        )
        params = {"caller_id": caller_id, "query_vector": query, "top_k": top_k}
    else:
        statement = (
            text(
                """
                SELECT item_id
                FROM asset_vector_items
                WHERE caller_id = :caller_id
                  AND item_id IN :candidate_item_ids
                ORDER BY embedding <=> CAST(:query_vector AS vector), item_id ASC
                LIMIT :top_k
                """
            )
            .bindparams(bindparam("candidate_item_ids", expanding=True))
        )
        params = {
            "caller_id": caller_id,
            "query_vector": query,
            "candidate_item_ids": candidate_item_ids,
            "top_k": top_k,
        }
    rows = await db.execute(statement, params)
    return [str(row.item_id) for row in rows]
