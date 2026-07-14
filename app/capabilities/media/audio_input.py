from __future__ import annotations

import io
from dataclasses import dataclass

import numpy as np

from app.core.config import settings
from app.core.exceptions import AppError
from app.integrations.object_storage import sha256_digest
from app.jobs.payload_adapters.oss_url_ref import canonical_ref_from_oss_url_ref
from app.jobs.types.audio_stem_separation.errors import (
    AUDIO_STEM_DURATION_EXCEEDS_LIMIT,
    AUDIO_STEM_INFERENCE_FAILED,
    AUDIO_STEM_INPUT_INVALID,
)
from app.schemas.jobs import (
    AudioStemSeparationInputObject,
    AudioWavInputPlanSnapshot,
    CanonicalObjectRefSnapshot,
    MediaFetchSpec,
)
from app.tools.object_storage import read_object_bytes

AUDIO_WAV_CONTENT_TYPE = "audio/wav"
MEDIA_AUDIO_INPUT_CAPABILITY_REF = "media.audio_input:1"
OBJECT_STORAGE_READ_TOOL_REF = "object_storage_read:1"


@dataclass(frozen=True)
class PreparedAudioInput:
    data: np.ndarray
    sample_rate: int
    duration_seconds: float


def build_audio_wav_input_plan(
    input_audio: AudioStemSeparationInputObject,
    *,
    max_duration_seconds: float | None,
) -> dict:
    ref = _canonical_input_ref(input_audio)
    if ref.content_hash is None:
        raise AppError(AUDIO_STEM_INPUT_INVALID, "audio stem input content_hash is required")
    plan = AudioWavInputPlanSnapshot(
        source=CanonicalObjectRefSnapshot(
            provider=ref.provider,
            bucket=ref.bucket,
            region=ref.region,
            key=ref.key,
            content_type=AUDIO_WAV_CONTENT_TYPE,
            content_hash=ref.content_hash,
        ),
        fetch=MediaFetchSpec(max_bytes=settings.job.oss_input_max_bytes),
        max_duration_seconds=max_duration_seconds,
    )
    return plan.model_dump(exclude_none=True)


def prepare_audio_wav_input(plan: AudioWavInputPlanSnapshot | dict) -> PreparedAudioInput:
    snapshot = AudioWavInputPlanSnapshot.model_validate(plan)
    source = snapshot.source
    data = read_object_bytes(
        bucket=source.bucket,
        region=source.region,
        key=source.key,
        max_bytes=snapshot.fetch.max_bytes,
    )
    if sha256_digest(data) != source.content_hash:
        raise AppError("INPUT_HASH_MISMATCH", "audio stem input sha256 mismatch")
    try:
        import soundfile as sf
    except ModuleNotFoundError as exc:
        raise AppError(
            AUDIO_STEM_INFERENCE_FAILED,
            "soundfile is not installed; run: uv sync --extra audio-separation",
        ) from exc
    try:
        audio, sample_rate = sf.read(io.BytesIO(data), dtype="float32", always_2d=True)
    except Exception as exc:
        raise AppError(AUDIO_STEM_INPUT_INVALID, "audio stem input must be a readable WAV file") from exc
    if sample_rate != 44100:
        raise AppError(
            AUDIO_STEM_INPUT_INVALID,
            "audio stem input sample_rate must be 44100",
            details={"actual": sample_rate, "expected": 44100},
        )
    if audio.ndim != 2 or audio.shape[1] != 2:
        actual_channels = int(audio.shape[1]) if audio.ndim == 2 else None
        raise AppError(
            AUDIO_STEM_INPUT_INVALID,
            "audio stem input must be stereo",
            details={"actual": actual_channels, "expected": 2},
        )
    duration_seconds = float(audio.shape[0] / sample_rate)
    if snapshot.max_duration_seconds is not None and duration_seconds > snapshot.max_duration_seconds:
        raise AppError(
            AUDIO_STEM_DURATION_EXCEEDS_LIMIT,
            "audio stem input duration exceeds max_duration_seconds",
            details={"actual": duration_seconds, "max_duration_seconds": snapshot.max_duration_seconds},
        )
    return PreparedAudioInput(data=audio.T.astype(np.float32), sample_rate=int(sample_rate), duration_seconds=duration_seconds)


def _canonical_input_ref(input_audio: AudioStemSeparationInputObject):
    try:
        return canonical_ref_from_oss_url_ref(
            input_audio.model_dump(),
            allowed_buckets=settings.job.audio_stem_separation_allowed_oss_buckets,
            allowed_regions=settings.job.audio_stem_separation_allowed_oss_regions,
            allowed_content_types={AUDIO_WAV_CONTENT_TYPE},
            public_endpoint=settings.storage.oss_public_endpoint or None,
            public_endpoint_bucket=getattr(settings.storage, "oss_bucket", "") or None,
            public_endpoint_region=getattr(settings.storage, "oss_region", "") or None,
        )
    except AppError as exc:
        raise AppError(
            AUDIO_STEM_INPUT_INVALID,
            "audio stem input_audio is invalid",
            details={"source_reason": exc.code, **(exc.details or {})},
        ) from exc
