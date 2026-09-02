from app.core.error_registry import ErrorSpec, register_error_specs

POSTER_TITLE_IMAGE_REFERENCE_INVALID = "POSTER_TITLE_IMAGE_REFERENCE_INVALID"
POSTER_TITLE_IMAGE_DRAW_COUNT_EXCEEDS_LIMIT = "POSTER_TITLE_IMAGE_DRAW_COUNT_EXCEEDS_LIMIT"
POSTER_TITLE_IMAGE_ALL_ITEMS_FAILED = "POSTER_TITLE_IMAGE_ALL_ITEMS_FAILED"

POSTER_TITLE_IMAGE_ERROR_SPECS: dict[str, ErrorSpec] = {
    POSTER_TITLE_IMAGE_REFERENCE_INVALID: ErrorSpec(
        "110001",
        POSTER_TITLE_IMAGE_REFERENCE_INVALID,
        "poster title image reference invalid",
        400,
        scope="job",
        owner="poster_title_image",
    ),
    POSTER_TITLE_IMAGE_DRAW_COUNT_EXCEEDS_LIMIT: ErrorSpec(
        "110002",
        POSTER_TITLE_IMAGE_DRAW_COUNT_EXCEEDS_LIMIT,
        "poster title image draw_count exceeds limit",
        400,
        scope="job",
        owner="poster_title_image",
    ),
    POSTER_TITLE_IMAGE_ALL_ITEMS_FAILED: ErrorSpec(
        "110003",
        POSTER_TITLE_IMAGE_ALL_ITEMS_FAILED,
        "all poster title image items failed",
        502,
        scope="job",
        owner="poster_title_image",
    ),
}


def register_poster_title_image_errors() -> None:
    register_error_specs(POSTER_TITLE_IMAGE_ERROR_SPECS)
