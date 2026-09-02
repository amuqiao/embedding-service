from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.operations import operation_route_kwargs_for_spec
from app.business_packages.asset_vector.embedding_adapter import (
    asset_query_embedding,
    average_embeddings,
    text_query_embedding,
)
from app.business_packages.asset_vector.errors import QUERY_ITEM_NOT_INDEXED
from app.business_packages.asset_vector.operations import (
    ASSET_VECTOR_EXISTS_OPERATION,
    ASSET_VECTOR_LIST_IDS_OPERATION,
    ASSET_VECTOR_SEARCH_OPERATION,
)
from app.business_packages.asset_vector.repository import (
    existing_item_ids,
    list_item_ids,
    search_by_vector,
    vectors_for_item_ids,
)
from app.business_packages.asset_vector.schemas import (
    ASSET_VECTOR_DEFAULT_TOP_K,
    ASSET_VECTOR_MAX_ITEM_ID_LENGTH,
    ASSET_VECTOR_MAX_ITEMS,
    AssetVectorExistsItem,
    AssetVectorExistsRequest,
    AssetVectorExistsResponse,
    AssetVectorIdsResponse,
    AssetVectorSearchRequest,
    AssetVectorSearchResponse,
)
from app.core.database import get_db
from app.core.exceptions import AppError
from app.core.security import require_service_auth

router = APIRouter(tags=["asset-vector"])


async def _query_vector(payload: AssetVectorSearchRequest, db: AsyncSession, caller_id: str) -> list[float]:
    vectors: list[list[float]] = []
    if payload.text is not None:
        vectors.append(text_query_embedding(payload.text))
    if payload.asset is not None:
        vectors.append(asset_query_embedding(payload.asset))
    if payload.item_ids is not None:
        stored_vectors = await vectors_for_item_ids(db, caller_id=caller_id, item_ids=payload.item_ids)
        missing = sorted(set(payload.item_ids) - set(stored_vectors))
        if missing:
            raise AppError(
                QUERY_ITEM_NOT_INDEXED,
                "query item_ids are not indexed",
                details={"item_ids": missing},
            )
        vectors.extend(stored_vectors[item_id] for item_id in payload.item_ids)
    return average_embeddings(vectors)


@router.post(
    ASSET_VECTOR_SEARCH_OPERATION.path,
    **operation_route_kwargs_for_spec(ASSET_VECTOR_SEARCH_OPERATION),
)
async def search_asset_vectors(
    payload: AssetVectorSearchRequest,
    db: AsyncSession = Depends(get_db),
    caller_id: str = Depends(require_service_auth),
) -> AssetVectorSearchResponse:
    query_vector = await _query_vector(payload, db, caller_id)
    item_ids = await search_by_vector(
        db,
        caller_id=caller_id,
        query_vector=query_vector,
        top_k=payload.top_k or ASSET_VECTOR_DEFAULT_TOP_K,
        candidate_item_ids=payload.candidate_item_ids,
    )
    return AssetVectorSearchResponse(item_ids=item_ids)


@router.post(
    ASSET_VECTOR_EXISTS_OPERATION.path,
    **operation_route_kwargs_for_spec(ASSET_VECTOR_EXISTS_OPERATION),
)
async def check_asset_vectors_exist(
    payload: AssetVectorExistsRequest,
    db: AsyncSession = Depends(get_db),
    caller_id: str = Depends(require_service_auth),
) -> AssetVectorExistsResponse:
    existing = await existing_item_ids(db, caller_id=caller_id, item_ids=payload.item_ids)
    return AssetVectorExistsResponse(
        items=[
            AssetVectorExistsItem(
                item_id=item_id,
                exists=item_id in existing,
            )
            for item_id in payload.item_ids
        ]
    )


@router.get(
    ASSET_VECTOR_LIST_IDS_OPERATION.path,
    **operation_route_kwargs_for_spec(ASSET_VECTOR_LIST_IDS_OPERATION),
)
async def list_asset_vector_ids(
    db: AsyncSession = Depends(get_db),
    caller_id: str = Depends(require_service_auth),
    limit: int = Query(default=100, gt=0, le=ASSET_VECTOR_MAX_ITEMS),
    cursor: str | None = Query(default=None, min_length=1, max_length=ASSET_VECTOR_MAX_ITEM_ID_LENGTH),
) -> AssetVectorIdsResponse:
    item_ids, next_cursor = await list_item_ids(db, caller_id=caller_id, limit=limit, cursor=cursor)
    return AssetVectorIdsResponse(item_ids=item_ids, next_cursor=next_cursor)
