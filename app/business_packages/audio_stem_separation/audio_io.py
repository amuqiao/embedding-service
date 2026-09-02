from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.tools.private.media_audio import SUPPORTED_AUDIO_INPUT_CONTENT_TYPES

AUDIO_WAV_CONTENT_TYPE = "audio/wav"
AUDIO_INPUT_CONTENT_TYPES = SUPPORTED_AUDIO_INPUT_CONTENT_TYPES
AUDIO_DECODE_NORMALIZE_TOOL_REF = "audio_decode_normalize:1"


@dataclass(frozen=True)
class PreparedAudioInput:
    data: np.ndarray
    sample_rate: int
    duration_seconds: float


__all__ = [
    "AUDIO_DECODE_NORMALIZE_TOOL_REF",
    "AUDIO_INPUT_CONTENT_TYPES",
    "AUDIO_WAV_CONTENT_TYPE",
    "PreparedAudioInput",
]
