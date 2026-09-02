from __future__ import annotations

from app.core.error_registry import ErrorSpec, register_error_specs

QUERY_ITEM_NOT_INDEXED = "QUERY_ITEM_NOT_INDEXED"

ASSET_VECTOR_ERROR_SPECS: dict[str, ErrorSpec] = {
    QUERY_ITEM_NOT_INDEXED: ErrorSpec(
        "114001",
        QUERY_ITEM_NOT_INDEXED,
        "query item is not indexed",
        404,
        scope="http",
        owner="asset_vector_search",
    ),
}


def register_asset_vector_errors() -> None:
    register_error_specs(ASSET_VECTOR_ERROR_SPECS)
