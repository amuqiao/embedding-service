from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.business_packages.audio_stem_separation.schemas import (
    AudioStemSeparationDurationMs,
    AudioStemSeparationParams,
    AudioStemSeparationStemOutputs,
)
from app.schemas.common import StrictBaseModel
from app.schemas.jobs import RuntimeFieldsBase
from app.tools.private.audio_contracts import AudioInputPlanSnapshot


class AudioStemSeparationTritonParams(AudioStemSeparationParams):
    pass


class AudioStemSeparationTritonRuntimeFields(RuntimeFieldsBase):
    operation: Literal["audio_stem_separation_triton"] = "audio_stem_separation_triton"
    media_input_plan: AudioInputPlanSnapshot
    onnx_model_version: str = Field(min_length=1, max_length=128)
    model_service: Literal["triton"] = "triton"
    triton_model_version: str = Field(min_length=1, max_length=64)
    segment_seconds: float = Field(gt=0)
    overlap_ratio: float = Field(gt=0, lt=1)


class AudioStemSeparationTritonResult(StrictBaseModel):
    schema_version: Literal["default"] = "default"
    job_type: Literal["audio_stem_separation_triton"] = "audio_stem_separation_triton"
    stems: AudioStemSeparationStemOutputs
    source_duration_seconds: float = Field(gt=0)
    segment_count: int = Field(ge=1)
    sample_rate: Literal[44100] = 44100
    channels: Literal[2] = 2
    onnx_model_version: str = Field(min_length=1, max_length=128)
    model_service: Literal["triton"] = "triton"
    triton_model_version: str = Field(min_length=1, max_length=64)
    duration_ms: AudioStemSeparationDurationMs


SCHEMAS = (
    AudioStemSeparationTritonParams,
    AudioStemSeparationTritonRuntimeFields,
    AudioStemSeparationTritonResult,
)
