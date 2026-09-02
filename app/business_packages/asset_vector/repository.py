from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.business_packages.asset_vector.embedding_adapter import cosine_similarity, item_embedding, item_index_text
from app.business_packages.asset_vector.models import AssetVectorItem
from app.business_packages.asset_vector.schemas import AssetVectorUpsertItemParams


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _embedding_from_db(value: Any) -> list[float]:
    decoded = json.loads(value) if isinstance(value, str) else value
    if not isinstance(decoded, list):
        raise ValueError("stored embedding must be a list")
    return [float(item) for item in decoded]


async def upsert_items(
    db: AsyncSession,
    *,
    caller_id: str,
    items: list[AssetVectorUpsertItemParams],
) -> list[tuple[str, str]]:
    indexed_at = _now_iso()
    table = AssetVectorItem.__table__
    statement = insert(table).values(
        [
            {
                "caller_id": caller_id,
                "item_id": item.item_id,
                "item_name": item.item_name,
                "asset": item.asset.model_dump(exclude_none=True),
                "labels": [label.model_dump(exclude_none=True) for label in item.labels],
                "metadata": {} if item.metadata is None else item.metadata,
                "embedding": item_embedding(item),
                "embedding_text": item_index_text(item),
            }
            for item in items
        ]
    )
    await db.execute(
        statement.on_conflict_do_update(
            index_elements=[table.c.caller_id, table.c.item_id],
            set_={
                table.c.item_name: statement.excluded.item_name,
                table.c.asset: statement.excluded.asset,
                table.c.labels: statement.excluded.labels,
                table.c.metadata: statement.excluded["metadata"],
                table.c.embedding: statement.excluded.embedding,
                table.c.embedding_text: statement.excluded.embedding_text,
                table.c.updated_at: func.now(),
            },
        )
    )
    await db.flush()
    return [(item.item_id, indexed_at) for item in items]


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
    rows = await db.execute(
        select(AssetVectorItem.item_id, AssetVectorItem.embedding).where(
            AssetVectorItem.caller_id == caller_id,
            AssetVectorItem.item_id.in_(item_ids),
        )
    )
    return {str(row.item_id): _embedding_from_db(row.embedding) for row in rows}


async def search_by_vector(
    db: AsyncSession,
    *,
    caller_id: str,
    query_vector: list[float],
    top_k: int,
    candidate_item_ids: list[str] | None,
) -> list[str]:
    if candidate_item_ids is not None:
        if not candidate_item_ids:
            return []
        statement = select(AssetVectorItem.item_id, AssetVectorItem.embedding).where(
            AssetVectorItem.caller_id == caller_id,
            AssetVectorItem.item_id.in_(candidate_item_ids),
        )
    else:
        statement = select(AssetVectorItem.item_id, AssetVectorItem.embedding).where(
            AssetVectorItem.caller_id == caller_id
        )
    scored: list[tuple[float, str]] = []
    for row in await db.execute(statement):
        embedding = _embedding_from_db(row.embedding)
        scored.append((cosine_similarity(query_vector, embedding), str(row.item_id)))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [item_id for _, item_id in scored[:top_k]]
