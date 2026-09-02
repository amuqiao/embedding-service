from app.core.error_registry import ErrorSpec, register_error_specs

ASSET_IMAGE_TAGGING_ALL_ITEMS_FAILED = "ASSET_IMAGE_TAGGING_ALL_ITEMS_FAILED"
ASSET_IMAGE_TAGGING_ITEMS_EXCEEDS_LIMIT = "ASSET_IMAGE_TAGGING_ITEMS_EXCEEDS_LIMIT"

ASSET_IMAGE_TAGGING_ERROR_SPECS: dict[str, ErrorSpec] = {
    ASSET_IMAGE_TAGGING_ALL_ITEMS_FAILED: ErrorSpec(
        "113001",
        ASSET_IMAGE_TAGGING_ALL_ITEMS_FAILED,
        "all asset image tagging items failed",
        502,
        scope="job",
        owner="asset_image_tagging",
    ),
    ASSET_IMAGE_TAGGING_ITEMS_EXCEEDS_LIMIT: ErrorSpec(
        "113002",
        ASSET_IMAGE_TAGGING_ITEMS_EXCEEDS_LIMIT,
        "asset image tagging items exceed configured limit",
        400,
        scope="request",
        owner="asset_image_tagging",
    ),
}


def register_asset_image_tagging_errors() -> None:
    register_error_specs(ASSET_IMAGE_TAGGING_ERROR_SPECS)
