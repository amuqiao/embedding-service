from __future__ import annotations


def register_all_capabilities() -> None:
    """Register all capability definitions."""
    from app.capabilities.definitions import CapabilityDefinition
    from app.capabilities.media.audio_input import MEDIA_AUDIO_INPUT_CAPABILITY_REF, OBJECT_STORAGE_READ_TOOL_REF
    from app.capabilities.registry import register
    from app.jobs.types.audio_stem_separation.errors import (
        AUDIO_STEM_DURATION_EXCEEDS_LIMIT,
        AUDIO_STEM_INFERENCE_FAILED,
        AUDIO_STEM_INPUT_INVALID,
    )

    register(
        CapabilityDefinition(
            capability_ref=MEDIA_AUDIO_INPUT_CAPABILITY_REF,
            plan_schema="AudioWavInputPlanSnapshot",
            result_schema="PreparedAudioInputMetadata",
            service_entrypoint="app.capabilities.media.audio_input:prepare_audio_wav_input",
            allowed_tool_refs=frozenset({OBJECT_STORAGE_READ_TOOL_REF}),
            error_codes=frozenset(
                {
                    "INPUT_HASH_MISMATCH",
                    "INPUT_TOO_LARGE",
                    "OSS_BUCKET_NOT_CONFIGURED",
                    "OSS_FETCH_FAILED",
                    "OSS_OBJECT_NOT_FOUND",
                    "OSS_REGION_NOT_CONFIGURED",
                    AUDIO_STEM_DURATION_EXCEEDS_LIMIT,
                    AUDIO_STEM_INFERENCE_FAILED,
                    AUDIO_STEM_INPUT_INVALID,
                }
            ),
        )
    )
