from __future__ import annotations

import hashlib
from dataclasses import dataclass

import numpy as np

from app.core.exceptions import AppError
from app.schemas.jobs import AudioInputPlanSnapshot
from app.tools.private.media_audio import SUPPORTED_AUDIO_INPUT_CONTENT_TYPES, decode_normalize_audio
from app.tools.private.object_storage_read import read_object_bytes

AUDIO_WAV_CONTENT_TYPE = "audio/wav"
AUDIO_INPUT_CONTENT_TYPES = SUPPORTED_AUDIO_INPUT_CONTENT_TYPES
OBJECT_STORAGE_READ_TOOL_REF = "object_storage_read:1"
AUDIO_DECODE_NORMALIZE_TOOL_REF = "audio_decode_normalize:1"


@dataclass(frozen=True)
class PreparedAudioInput:
    data: np.ndarray
    sample_rate: int
    duration_seconds: float


def _sha256_digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def prepare_audio_input(plan: AudioInputPlanSnapshot | dict) -> PreparedAudioInput:
    snapshot = AudioInputPlanSnapshot.model_validate(plan)
    source = snapshot.source
    data = read_object_bytes(
        bucket=source.bucket,
        region=source.region,
        key=source.key,
        max_bytes=snapshot.fetch.max_bytes,
    )
    if _sha256_digest(data) != source.content_hash:
        raise AppError("INPUT_HASH_MISMATCH", "audio stem input sha256 mismatch")
    if source.content_type != snapshot.decode.source_content_type:
        raise AppError(
            "AUDIO_STEM_INPUT_INVALID",
            "audio input decode source_content_type mismatch",
            details={"source": source.content_type, "decode": snapshot.decode.source_content_type},
        )
    decoded = decode_normalize_audio(
        {
            "data": data,
            "decode": snapshot.decode.model_dump(),
            "max_duration_seconds": snapshot.max_duration_seconds,
        }
    )
    return PreparedAudioInput(data=decoded.data, sample_rate=decoded.sample_rate, duration_seconds=decoded.duration_seconds)


__all__ = [
    "AUDIO_DECODE_NORMALIZE_TOOL_REF",
    "AUDIO_INPUT_CONTENT_TYPES",
    "AUDIO_WAV_CONTENT_TYPE",
    "OBJECT_STORAGE_READ_TOOL_REF",
    "PreparedAudioInput",
    "prepare_audio_input",
]
