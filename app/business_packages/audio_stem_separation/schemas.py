from __future__ import annotations

import re
from typing import Literal

from pydantic import Field, field_validator

from app.schemas.common import StrictBaseModel
from app.schemas.jobs import RuntimeFieldsBase
from app.business_packages.audio_stem_separation.audio_contracts import (
    SCHEMAS as AUDIO_INPUT_SCHEMAS,
    AudioInputPlanSnapshot,
)
from app.tools.private.media_audio import AudioInputContentType

BARE_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class AudioStemSeparationInputObject(StrictBaseModel):
    public_url: str = Field(min_length=1)
    internal_url: str = Field(min_length=1)
    content_type: AudioInputContentType
    sha256: str

    @field_validator("sha256")
    @classmethod
    def validate_bare_sha256(cls, value: str) -> str:
        if not BARE_HASH_RE.fullmatch(value):
            raise ValueError("sha256 must be 64 lowercase hex characters")
        return value


class AudioStemSeparationParams(StrictBaseModel):
    input_audio: AudioStemSeparationInputObject
    max_duration_seconds: float | None = Field(default=None, gt=0, le=3600)


class AudioStemSeparationRuntimeFields(RuntimeFieldsBase):
    operation: Literal["audio_stem_separation"] = "audio_stem_separation"
    media_input_plan: AudioInputPlanSnapshot
    onnx_model_version: str = Field(min_length=1, max_length=128)
    execution_provider: str = Field(min_length=1, max_length=128)
    segment_seconds: float = Field(gt=0)
    overlap_ratio: float = Field(gt=0, lt=1)


class AudioStemSeparationOutputObject(StrictBaseModel):
    public_url: str
    internal_url: str
    content_type: Literal["audio/wav"]
    sha256: str

    @field_validator("sha256")
    @classmethod
    def validate_output_sha256(cls, value: str) -> str:
        if not BARE_HASH_RE.fullmatch(value):
            raise ValueError("sha256 must be 64 lowercase hex characters")
        return value


class AudioStemSeparationStemOutputs(StrictBaseModel):
    drums: AudioStemSeparationOutputObject
    bass: AudioStemSeparationOutputObject
    other: AudioStemSeparationOutputObject
    vocals: AudioStemSeparationOutputObject


class AudioStemSeparationDurationMs(StrictBaseModel):
    io: int = Field(ge=0)
    inference: int = Field(ge=0)
    total: int = Field(ge=0)


class AudioStemSeparationResult(StrictBaseModel):
    schema_version: Literal["default"] = "default"
    job_type: Literal["audio_stem_separation"] = "audio_stem_separation"
    stems: AudioStemSeparationStemOutputs
    source_duration_seconds: float = Field(gt=0)
    segment_count: int = Field(ge=1)
    sample_rate: Literal[44100] = 44100
    channels: Literal[2] = 2
    onnx_model_version: str = Field(min_length=1, max_length=128)
    execution_provider: str = Field(min_length=1, max_length=128)
    duration_ms: AudioStemSeparationDurationMs


SCHEMAS = AUDIO_INPUT_SCHEMAS + (
    AudioStemSeparationInputObject,
    AudioStemSeparationParams,
    AudioStemSeparationRuntimeFields,
    AudioStemSeparationOutputObject,
    AudioStemSeparationStemOutputs,
    AudioStemSeparationDurationMs,
    AudioStemSeparationResult,
)
