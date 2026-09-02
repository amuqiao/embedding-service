from __future__ import annotations

from app.api.operations import OperationSpec

ASSET_VECTOR_AUTH_BOUNDARY = "service bearer token (locally disable-able) + caller id header (optionally ignored)"
ASSET_VECTOR_COMMON_ERRORS = frozenset({"UNAUTHORIZED", "FORBIDDEN", "INVALID_INPUT", "INTERNAL_ERROR"})

ASSET_VECTOR_SEARCH_OPERATION = OperationSpec(
    operation_id="asset_vector_search",
    channel="http",
    method="POST",
    path="/vector-search",
    success_status=200,
    auth_boundary=ASSET_VECTOR_AUTH_BOUNDARY,
    request_schema="AssetVectorSearchRequest",
    response_data_schema="AssetVectorSearchResponse",
    error_codes=ASSET_VECTOR_COMMON_ERRORS | frozenset({"QUERY_ITEM_NOT_INDEXED"}),
    idempotency_key=None,
    side_effects=(),
    log_events=("request_completed", "request_failed"),
)

ASSET_VECTOR_EXISTS_OPERATION = OperationSpec(
    operation_id="asset_vector_assets_exists",
    channel="http",
    method="POST",
    path="/vector-assets:exists",
    success_status=200,
    auth_boundary=ASSET_VECTOR_AUTH_BOUNDARY,
    request_schema="AssetVectorExistsRequest",
    response_data_schema="AssetVectorExistsResponse",
    error_codes=ASSET_VECTOR_COMMON_ERRORS,
    idempotency_key=None,
    side_effects=(),
    log_events=("request_completed", "request_failed"),
)

ASSET_VECTOR_LIST_IDS_OPERATION = OperationSpec(
    operation_id="asset_vector_asset_ids",
    channel="http",
    method="GET",
    path="/vector-assets/ids",
    success_status=200,
    auth_boundary=ASSET_VECTOR_AUTH_BOUNDARY,
    request_schema=None,
    response_data_schema="AssetVectorIdsResponse",
    error_codes=ASSET_VECTOR_COMMON_ERRORS,
    idempotency_key=None,
    side_effects=(),
    log_events=("request_completed", "request_failed"),
)

OPERATIONS = (
    ASSET_VECTOR_SEARCH_OPERATION,
    ASSET_VECTOR_EXISTS_OPERATION,
    ASSET_VECTOR_LIST_IDS_OPERATION,
)
