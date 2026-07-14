from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from app.capabilities.media.audio_input import AUDIO_INPUT_CONTENT_TYPES
from app.core.config import settings
from app.core.exceptions import AppError
from app.jobs.payload_adapters.oss_url_ref import canonical_ref_from_oss_url_ref
from app.jobs.types.audio_stem_separation.errors import (
    AUDIO_STEM_INFERENCE_FAILED,
    AUDIO_STEM_INPUT_INVALID,
    AUDIO_STEM_MODEL_ASSET_MISSING,
    AUDIO_STEM_OUTPUT_INVALID,
)
from app.schemas.jobs import (
    AudioDecodeNormalizeSpec,
    AudioInputPlanSnapshot,
    AudioStemSeparationInputObject,
    CanonicalObjectRefSnapshot,
    MediaFetchSpec,
)

MODEL_ASSET_PATH = Path(__file__).with_name("audio_stem_separation") / "model_asset.yaml"
SOURCES = ("drums", "bass", "other", "vocals")
DEFAULT_TIMEOUT_SECONDS = 2400


def build_audio_input_plan(
    input_audio: AudioStemSeparationInputObject,
    *,
    max_duration_seconds: float | None,
) -> dict:
    ref = _canonical_input_ref(input_audio)
    if ref.content_hash is None:
        raise AppError(AUDIO_STEM_INPUT_INVALID, "audio stem input content_hash is required")
    plan = AudioInputPlanSnapshot(
        source=CanonicalObjectRefSnapshot(
            provider=ref.provider,
            bucket=ref.bucket,
            region=ref.region,
            key=ref.key,
            content_type=ref.content_type,
            content_hash=ref.content_hash,
        ),
        fetch=MediaFetchSpec(max_bytes=settings.job.oss_input_max_bytes),
        decode=AudioDecodeNormalizeSpec(source_content_type=ref.content_type),
        max_duration_seconds=max_duration_seconds,
    )
    return plan.model_dump(exclude_none=True)


def _canonical_input_ref(input_audio: AudioStemSeparationInputObject):
    try:
        return canonical_ref_from_oss_url_ref(
            input_audio.model_dump(),
            allowed_buckets=settings.job.audio_stem_separation_allowed_oss_buckets,
            allowed_regions=settings.job.audio_stem_separation_allowed_oss_regions,
            allowed_content_types=AUDIO_INPUT_CONTENT_TYPES,
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


def load_model_asset() -> dict[str, Any]:
    try:
        with MODEL_ASSET_PATH.open("r", encoding="utf-8") as handle:
            asset = yaml.safe_load(handle)
    except OSError as exc:
        raise AppError(AUDIO_STEM_MODEL_ASSET_MISSING, "audio stem model_asset.yaml is missing") from exc
    if not isinstance(asset, dict):
        raise AppError(AUDIO_STEM_MODEL_ASSET_MISSING, "audio stem model_asset.yaml is invalid")
    return asset


def segment_ranges(*, total_len: int, segment_samples: int, stride: int) -> list[tuple[int, int]]:
    if total_len < 1:
        raise AppError(AUDIO_STEM_INPUT_INVALID, "audio input must not be empty")
    segment_count = 1 + max(0, (total_len - segment_samples + stride - 1) // stride)
    return [
        (index * stride, min(index * stride + segment_samples, total_len))
        for index in range(segment_count)
    ]


def make_transition_window(segment_samples: int, overlap_ratio: float) -> np.ndarray:
    transition = int(segment_samples * overlap_ratio)
    window = np.ones(segment_samples, dtype=np.float32)
    fade = np.linspace(0, 1, transition, dtype=np.float32)
    window[:transition] = fade
    window[-transition:] = fade[::-1]
    return window


def chunk_window(
    window: np.ndarray,
    *,
    chunk_len: int,
    overlap: int,
    is_first: bool,
    is_last: bool,
) -> np.ndarray:
    result = window[:chunk_len].copy()
    edge = min(overlap, chunk_len)
    if is_first:
        result[:edge] = 1.0
    if is_last:
        result[-edge:] = 1.0
    return result


def wav_bytes(audio: np.ndarray, *, sample_rate: int) -> bytes:
    try:
        import soundfile as sf
    except ModuleNotFoundError as exc:
        raise AppError(
            AUDIO_STEM_INFERENCE_FAILED,
            "soundfile is not installed; run: uv sync --extra audio-separation",
        ) from exc
    buffer = io.BytesIO()
    try:
        sf.write(buffer, audio.T, sample_rate, format="WAV", subtype="PCM_16")
    except Exception as exc:
        raise AppError(AUDIO_STEM_OUTPUT_INVALID, "failed to encode audio stem WAV") from exc
    return buffer.getvalue()
