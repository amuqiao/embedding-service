from app.core.error_registry import ErrorSpec, register_error_specs
from app.capabilities.media.error_codes import (
    AUDIO_STEM_DURATION_EXCEEDS_LIMIT,
    AUDIO_STEM_INFERENCE_FAILED,
    AUDIO_STEM_INPUT_INVALID,
    AUDIO_STEM_MODEL_ASSET_MISSING,
    AUDIO_STEM_OUTPUT_INVALID,
    AUDIO_STEM_RUNTIME_UNAVAILABLE,
)

AUDIO_STEM_ERROR_SPECS: dict[str, ErrorSpec] = {
    AUDIO_STEM_INPUT_INVALID: ErrorSpec(
        "111001",
        AUDIO_STEM_INPUT_INVALID,
        "audio stem input invalid",
        400,
        scope="job",
        owner="audio_stem_separation",
    ),
    AUDIO_STEM_DURATION_EXCEEDS_LIMIT: ErrorSpec(
        "111002",
        AUDIO_STEM_DURATION_EXCEEDS_LIMIT,
        "audio stem duration exceeds limit",
        400,
        scope="job",
        owner="audio_stem_separation",
    ),
    AUDIO_STEM_MODEL_ASSET_MISSING: ErrorSpec(
        "111003",
        AUDIO_STEM_MODEL_ASSET_MISSING,
        "audio stem model asset missing or invalid",
        500,
        scope="job",
        owner="audio_stem_separation",
    ),
    AUDIO_STEM_INFERENCE_FAILED: ErrorSpec(
        "111004",
        AUDIO_STEM_INFERENCE_FAILED,
        "audio stem inference failed",
        502,
        scope="job",
        owner="audio_stem_separation",
    ),
    AUDIO_STEM_OUTPUT_INVALID: ErrorSpec(
        "111005",
        AUDIO_STEM_OUTPUT_INVALID,
        "audio stem output invalid",
        502,
        scope="job",
        owner="audio_stem_separation",
    ),
    AUDIO_STEM_RUNTIME_UNAVAILABLE: ErrorSpec(
        "111006",
        AUDIO_STEM_RUNTIME_UNAVAILABLE,
        "audio stem runtime unavailable",
        500,
        scope="job",
        owner="audio_stem_separation",
    ),
}


def register_audio_stem_separation_errors() -> None:
    register_error_specs(AUDIO_STEM_ERROR_SPECS)
