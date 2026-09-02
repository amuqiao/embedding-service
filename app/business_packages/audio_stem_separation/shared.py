from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from app.core.exceptions import AppError
from app.business_packages.audio_stem_separation.errors import (
    AUDIO_STEM_INFERENCE_FAILED,
    AUDIO_STEM_INPUT_INVALID,
    AUDIO_STEM_MODEL_ASSET_MISSING,
    AUDIO_STEM_OUTPUT_INVALID,
)

MODEL_ASSET_PATH = Path(__file__).with_name("model_asset.yaml")
SOURCES = ("drums", "bass", "other", "vocals")
DEFAULT_TIMEOUT_SECONDS = 2400


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
